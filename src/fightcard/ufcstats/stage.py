"""`fightcard scrape`: ufcstats → data/events/*.json, fighters.json, index.json.

Everything is fetched and validated in memory first; nothing is written unless every fetch and check
succeeds (non-zero exit = do not commit, do not deploy).

With a history store (`--history-db`, default `<--data>/../history/history.sqlite` as for `score`, seeded by
`fightcard history ingest`; `--no-history` to go without)
the run also keeps it current, in one transaction (write lock taken up front, waiting up to `BUSY_TIMEOUT_MS`)
committed before data/ is written, so data/ is never ahead of the store: the full completed
listing (refetched every run: it says which events are UFC events), every event page and every fighter page
fetched, through the same parse + upsert path as `history ingest`. Completed events in data/ with no bout in
the store are refetched so `score` finds them (backfill: up to `BACKFILL_PER_RUN` a run; one whose page 404s or
lacks an outcome is skipped with a warning). A completed bout with a method but no W/L outcome on this run's
cards fails the run with nothing written, as it fails `history ingest`. If writing data/ fails after the commit, the run exits 1
saying so; the store being ahead of data/ is harmless, and the next run rewrites data/.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fightcard.history.ingest import (
    LISTING_URL,
    MissingOutcome,
    listed_ids,
    page_path,
    record_page,
    upsert_card,
    upsert_fighter_page,
    without_outcome,
)
from fightcard.history.store import connect
from fightcard.models import Event, Fighter, Index
from fightcard.store import Store
from fightcard.ufcstats.client import Client, Halt, HttpError
from fightcard.ufcstats.parsers import (
    parse_event_detail,
    parse_event_list,
    ParsedEvent,
    parse_fighter,
    slugify,
)

log = logging.getLogger(__name__)

BASE_URL = "http://ufcstats.com"
UPCOMING_URL = f"{BASE_URL}/statistics/events/upcoming"
COMPLETED_URL = LISTING_URL  # ?page=all: the whole listing, as `history ingest` reads it
FIGHTER_MAX_AGE = timedelta(days=30)
BACKFILL_PER_RUN = 20  # completed events refetched per run for the history store (~1 request each)
BUSY_TIMEOUT_MS = 30_000  # how long to wait for another writer to release the history store


class ValidationError(Exception):
    pass


class DataWriteFailed(Exception):
    """Writing data/ failed after the store (if any) was committed; the message says so."""

    def __init__(self, exc: OSError, store_updated: bool):
        store = "store updated, " if store_updated else ""
        super().__init__(f"{store}data/ may be partially written; the next run rewrites it: {exc}")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_url(event_id: str) -> str:
    return f"{BASE_URL}/event-details/{event_id}"


def fighter_url(fighter_id: str) -> str:
    return f"{BASE_URL}/fighter-details/{fighter_id}"


def make_client(cookie_file: Path | None) -> Client:
    return Client(cookie_file=cookie_file)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recent", type=int, default=1,
                        help="also scrape results for the N most recent completed events")
    parser.add_argument("--cookie-file", type=Path, default=None,
                        help="where to cache the ufcstats _fmc cookie (default ~/.cache/fightcard/)")
    history = parser.add_mutually_exclusive_group()
    history.add_argument("--history-db", type=Path, default=None,
                         help="bout-history store to keep current (seed it with fightcard history ingest); "
                              "default <--data>/../history/history.sqlite, as for score")
    history.add_argument("--no-history", action="store_true", help="scrape without a history store")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.no_history:
        return scrape(Store(args.data), make_client(args.cookie_file), now=utcnow(), recent=args.recent)
    db = args.history_db or args.data.resolve().parent / "history" / "history.sqlite"
    if not db.is_file():
        print(f"scrape: no history store at {db}; seed it with `fightcard history ingest` "
              "or pass --no-history", file=sys.stderr)
        return 1
    history = connect(db)
    try:
        return scrape(Store(args.data), make_client(args.cookie_file), now=utcnow(), recent=args.recent,
                      history=history)
    finally:
        history.close()


# --- merging ---------------------------------------------------------------------


def merge_event(old: Event | None, new: Event) -> Event:
    """Keep finish tiers, card segments and pre-fight corner order for fights still on the card."""
    if old is None:
        return new
    previous = {f.id: f for f in old.fights}
    fights = []
    for fight in new.fights:
        prev = previous.get(fight.id)
        if prev is not None:
            update = {
                "finish_tier": prev.finish_tier,
                "tier_frozen_at": prev.tier_frozen_at,
                "segment": prev.segment,
            }
            # completed cards list the winner first; keep the corners the site already showed
            if (fight.red, fight.blue) == (prev.blue, prev.red):
                update |= {"red": prev.red, "blue": prev.blue}
            fight = fight.model_copy(update=update)
        fights.append(fight)
    return new.model_copy(update={"fights": fights})


def is_fresh(fighter: Fighter, now: datetime) -> bool:
    if not fighter.details_fetched_at or fighter.recent_fights is None:
        return False
    fetched = datetime.fromisoformat(fighter.details_fetched_at)
    return now - fetched < FIGHTER_MAX_AGE


# --- scraping ----------------------------------------------------------------------


class _Run:
    def __init__(self, store: Store, client, now: datetime, recent: int, history: sqlite3.Connection | None):
        self.store = store
        self.client = client
        self.now = now
        self.today = now.date().isoformat()
        self.recent = recent
        self.history = history
        self.existing = store.load_events()
        self.fighters = store.load_fighters()
        self.scraped: dict[str, Event] = {}
        self.card_names: dict[str, str] = {}
        self.refresh: set[str] = set()  # fighters whose record just changed
        self.upcoming_ids: list[str] = []
        self.backfilled: set[str] = set()  # completed events refetched only for the history store
        # for the history store: pages as fetched (url -> html) and as parsed
        self.pages: dict[str, str] = {}
        self.parsed: dict[str, ParsedEvent] = {}
        self.fighter_pages: dict[str, Fighter] = {}
        self.listed: set[str] = set()

    def _get(self, url: str) -> str:
        html = self.client.get(url)
        self.pages[url] = html
        return html

    def _detail(self, url: str, event_id: str) -> Event:
        parsed = parse_event_detail(self._get(url), event_id)
        self.parsed[event_id] = parsed
        for fid, name in parsed.fighters.items():
            self.card_names.setdefault(fid, name)
        return parsed.event

    def upcoming(self) -> None:
        stubs = parse_event_list(self.client.get(UPCOMING_URL))
        if not stubs:
            raise ValidationError("upcoming list parsed to 0 events")
        soonest = min(stubs, key=lambda s: s.date or "9999-99-99")
        for stub in stubs:
            event = self._detail(stub.url, stub.id)
            if not event.fights:
                if stub is soonest:
                    raise ValidationError(f"soonest upcoming event {stub.id} ({stub.name}) has 0 fights")
                # far-out cards often have no bouts yet: don't write, index or store them
                log.info("skipping event %s (%s): 0 fights announced", stub.id, stub.name)
                self._forget(stub.url, stub.id)
                continue
            name = stub.name or event.name
            self.scraped[stub.id] = event.model_copy(update={
                "status": "upcoming",
                "name": name,
                "slug": slugify(name),
                "date": stub.date or event.date,
                "location": stub.location or event.location,
            })
            self.upcoming_ids.append(stub.id)
        if not self.upcoming_ids:
            raise ValidationError("all upcoming events together have 0 fights")

    def results(self) -> None:
        stale = sorted(
            (e for e in self.existing.values()
             if e.status == "upcoming" and e.date < self.today and e.id not in self.scraped),
            key=lambda e: e.date,
        )
        targets = {e.id: event_url(e.id) for e in stale}
        if self.recent > 0 or self.history is not None:
            listing = self._get(COMPLETED_URL)
            self.listed = listed_ids(listing)
            past = [s for s in parse_event_list(listing) if s.date and s.date < self.today]
            for stub in past[: self.recent]:
                targets.setdefault(stub.id, stub.url)
        for event_id, url in targets.items():
            self._result(url, event_id)
        if self.history is not None:
            self.backfill()

    def backfill(self) -> None:
        """Completed events in data/ the store lacks, up to BACKFILL_PER_RUN, oldest first. They're not on this
        run's cards, so one that 404s or has a bout without an outcome is skipped with a warning (left out of the
        store and data/) instead of failing every run. Their fighters are fetched only when fighters.json lacks
        them."""
        rated = {eid for (eid,) in self.history.execute(
            "SELECT DISTINCT event_id FROM fights WHERE outcome IS NOT NULL")}
        todo = [e.id for e in sorted(self.existing.values(), key=lambda e: e.date)
                if e.status == "completed" and e.id not in rated and e.id not in self.scraped]
        if len(todo) > BACKFILL_PER_RUN:
            log.info("backfill: %d of %d events missing from the history store this run, %d in later runs",
                     BACKFILL_PER_RUN, len(todo), len(todo) - BACKFILL_PER_RUN)
        for event_id in todo[:BACKFILL_PER_RUN]:
            url = event_url(event_id)
            try:
                self._result(url, event_id)
            except HttpError as exc:
                if exc.status != 404:
                    raise
                log.warning("backfill: skipping event %s: %s", event_id, exc)
                continue
            no_outcome = without_outcome(self.parsed[event_id])
            if no_outcome:
                log.warning("backfill: skipping event %s: %s", event_id, MissingOutcome(no_outcome))
                self._forget(url, event_id)
                self.scraped.pop(event_id)
                continue
            self.backfilled.add(event_id)

    def _result(self, url: str, event_id: str) -> Event:
        event = self._detail(url, event_id)
        prev = self.existing.get(event_id)
        if event.status == "completed" and (prev is None or prev.status != "completed"):
            self.refresh |= {fid for f in event.fights for fid in (f.red, f.blue)}
        self.scraped[event_id] = event
        return event

    def _forget(self, url: str, event_id: str) -> None:
        """Keep a fetched event page out of the history store."""
        self.pages.pop(url, None)
        self.parsed.pop(event_id, None)

    def fighter_details(self) -> None:
        needed = dict.fromkeys(fid for e in self.scraped.values() for f in e.fights for fid in (f.red, f.blue))
        backfill_only = needed.keys() - {fid for eid, e in self.scraped.items() if eid not in self.backfilled
                                         for f in e.fights for fid in (f.red, f.blue)}
        for fid in needed:
            current = self.fighters.get(fid)
            if current and fid not in self.refresh and (is_fresh(current, self.now) or fid in backfill_only):
                continue
            try:
                fetched = parse_fighter(self._get(fighter_url(fid)), fid)
            except HttpError as exc:
                log.warning("fighter %s: %s", fid, exc)
                if current is None:
                    self.fighters[fid] = Fighter(id=fid, name=self.card_names.get(fid, fid))
                continue
            self.fighter_pages[fid] = fetched
            self.fighters[fid] = fetched.model_copy(update={
                "name": fetched.name or self.card_names.get(fid, fid),
                "image": current.image if current else None,
                "details_fetched_at": iso(self.now),
            })

    def write(self) -> dict[str, int]:
        """The store first, committed, then data/: data/ never gets ahead of the store (`score`'s staleness check)."""
        if self.history is not None:
            self.history.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
            self.history.execute("BEGIN IMMEDIATE")  # the write lock, before any upsert
            try:
                self._write_history()
                self.history.commit()
            except BaseException:
                self.history.rollback()
                raise
        try:
            return self._write_data()
        except OSError as exc:
            raise DataWriteFailed(exc, store_updated=self.history is not None) from exc

    def _write_history(self) -> None:
        """The run's pages in the order `history ingest` reads them: listing, event pages by id, fighter pages."""
        no_outcome = [fid for parsed in self.parsed.values() for fid in without_outcome(parsed)]
        if no_outcome:
            raise MissingOutcome(no_outcome)  # like `history ingest`: the rating would drop these silently
        stamp = iso(self.now)
        for url, html in self.pages.items():
            if "/fighter-details/" not in url or url.endswith(tuple(self.fighter_pages)):
                record_page(self.history, url, page_path(url), html.encode("utf-8"), stamp)
        for event_id in sorted(self.parsed):
            upsert_card(self.history, self.parsed[event_id], self.listed)
        for fid in sorted(self.fighter_pages):
            upsert_fighter_page(self.history, self.fighter_pages[fid], stamp)

    def _write_data(self) -> dict[str, int]:
        merged = {eid: merge_event(self.existing.get(eid), e) for eid, e in self.scraped.items()}
        events = {**self.existing, **merged}
        old_index = self.store.load_index()
        index = Index(
            generated_at=iso(self.now),
            upcoming=[eid for eid in self.upcoming_ids if events[eid].date >= self.today],
            recent=[e.id for e in sorted(
                (e for e in events.values() if e.status == "completed"),
                key=lambda e: e.date, reverse=True,
            )],
            model=old_index.model if old_index else None,
        )
        for event in merged.values():
            self.store.save_event(event)
        self.store.save_fighters(self.fighters)
        self.store.save_index(index)
        return {
            "events_written": len(merged),
            "upcoming": len(index.upcoming),
            "completed": len(index.recent),
            "fights": sum(len(e.fights) for e in merged.values()),
            "fighters": len(self.fighters),
            "requests": getattr(self.client, "requests", 0),
        }


def scrape(store: Store, client, now: datetime, recent: int = 1, history: sqlite3.Connection | None = None) -> int:
    run_ = _Run(store, client, now, recent, history)
    try:
        run_.upcoming()
        run_.results()
        run_.fighter_details()
        counts = run_.write()
    except (Halt, HttpError, ValidationError, MissingOutcome, sqlite3.Error) as exc:
        print(f"scrape failed, nothing written: {exc}", file=sys.stderr)
        return 1
    except DataWriteFailed as exc:
        print(f"scrape failed: {exc}", file=sys.stderr)
        return 1
    print("scrape ok: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0
