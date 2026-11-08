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
cards fails the run with nothing written, as it fails `history ingest`, except on a card dated within RESULTS_GRACE:
there it is a bout being fought or just scored, so it is held as not resulted yet (`hold_unresulted`, a warning) and
picked up once ufcstats posts the W/L flag. If writing data/ fails after the commit, the run exits 1
saying so; the store being ahead of data/ is harmless, and the next run rewrites data/.

Each bout's result is logged once, by the run that first writes it (`result_seen event=<id> fight=<id>
seen_at=<UTC>`), on cards dated within RESULTS_GRACE, so fight nights show how fast ufcstats posts results.

`--live` (`scrape_live`) is the fight-night fast loop's scrape (docs/SERVICE.md, "Live refresh"): only the pages of
the events `fightcard.service.live.due_events` names (indexed, dated today or yesterday, not fully resulted), no
listings and no fighter pages. When a bout's result (or the card) changed it writes those events, the store, a stub
for a fighter new to fighters.json, and the index with a new generated_at; otherwise nothing, and it exits
`live.UNCHANGED`. The hourly run refreshes the fighter pages of everyone with a result on a card within RESULTS_GRACE
whose page doesn't list a bout on that day yet, so records catch up whichever run saw the result first.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fightcard.history.ingest import (
    LISTING_URL,
    MissingOutcome,
    completed_ids,
    listed_ids,
    page_path,
    record_page,
    upsert_card,
    upsert_fighter_page,
    without_outcome,
)
from fightcard.history.store import complete_events, connect
from fightcard.models import Event, Fighter, Index
from fightcard.service import live
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
BACKFILL_RETRY = timedelta(days=7)  # a skipped backfill event is tried again after this
BUSY_TIMEOUT_MS = 30_000  # how long to wait for another writer to release the history store
RESULTS_GRACE = timedelta(days=2)  # a card awaiting results stays in index.upcoming this long past its date


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
    parser.add_argument("--live", action="store_true",
                        help="fight night: refetch only the live events' pages; exit "
                             f"{live.UNCHANGED} when no result changed (nothing written)")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.no_history:
        if getattr(args, "live", False):
            return scrape_live(Store(args.data), make_client(args.cookie_file), now=utcnow())
        return scrape(Store(args.data), make_client(args.cookie_file), now=utcnow(), recent=args.recent)
    db = args.history_db or args.data.resolve().parent / "history" / "history.sqlite"
    if not db.is_file():
        print(f"scrape: no history store at {db}; seed it with `fightcard history ingest` "
              "or pass --no-history", file=sys.stderr)
        return 1
    history = connect(db)
    try:
        if getattr(args, "live", False):
            return scrape_live(Store(args.data), make_client(args.cookie_file), now=utcnow(), history=history)
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


def hold_unresulted(parsed: ParsedEvent) -> ParsedEvent:
    """The page with each bout that has a method but no W/L outcome (`without_outcome`) taken as not resulted yet,
    and the status following: on a live card ufcstats can show the method before the flag."""
    held = set(without_outcome(parsed))
    if not held:
        return parsed
    for fight_id in sorted(held):
        log.warning("event %s: bout %s has a method but no W/L outcome yet; held as not resulted",
                    parsed.event.id, fight_id)
    fights = [f.model_copy(update={"result": None}) if f.id in held else f for f in parsed.event.fights]
    status = "completed" if any(f.result for f in fights) else "upcoming"
    return ParsedEvent(event=parsed.event.model_copy(update={"fights": fights, "status": status}),
                       fighters=parsed.fighters)


def log_seen(old: Event | None, new: Event, now: datetime) -> None:
    """`result_seen` for each bout of `new` with a result `old` didn't have."""
    before = {f.id for f in old.fights if f.result is not None} if old else set()
    for fight in new.fights:
        if fight.result is not None and fight.id not in before:
            log.info("result_seen event=%s fight=%s seen_at=%s", new.id, fight.id, iso(now))


def without_ratings(event: Event) -> Event:
    """The event without what score fills in from the store (elo, form): what a scrape compares."""
    return event.model_copy(update={"fights": [f.model_copy(update={"elo": None, "form": None})
                                               for f in event.fights]})


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
        self.cutoff = (now.date() - RESULTS_GRACE).isoformat()  # cards dated from here on are current
        self.recent = recent
        self.history = history
        self.existing = store.load_events()
        self.fighters = store.load_fighters()
        self.scraped: dict[str, Event] = {}
        self.card_names: dict[str, str] = {}
        self.refresh: set[str] = set()  # fighters whose record just changed
        self.upcoming_ids: list[str] = []
        self.backfilled: set[str] = set()  # completed events refetched only for the history store
        self.skipped: dict[str, str] = {}  # backfill events skipped this run -> why (`backfill_skips`)
        # for the history store: pages as fetched (url -> html) and as parsed
        self.pages: dict[str, str] = {}
        self.parsed: dict[str, ParsedEvent] = {}
        self.fighter_pages: dict[str, Fighter] = {}
        self.listed: set[str] = set()
        self.completed: set[str] = set()  # completed per the listing (`completed_ids`): settles empty cards

    def _get(self, url: str) -> str:
        html = self.client.get(url)
        self.pages[url] = html
        return html

    def _detail(self, url: str, event_id: str) -> Event:
        parsed = parse_event_detail(self._get(url), event_id)
        if parsed.event.date >= self.cutoff:
            parsed = hold_unresulted(parsed)
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
            # today's: a completed one may have only its first bouts' results yet
            (e for e in self.existing.values()
             if (e.date == self.today or (e.status == "upcoming" and e.date < self.today)) and e.id not in self.scraped),
            key=lambda e: e.date,
        )
        targets = {e.id: event_url(e.id) for e in stale}
        if self.recent > 0 or self.history is not None:
            listing = self._get(COMPLETED_URL)
            self.listed, self.completed = listed_ids(listing), completed_ids(listing)
            past = [s for s in parse_event_list(listing) if s.date and s.date < self.today]
            for stub in past[: self.recent]:
                targets.setdefault(stub.id, stub.url)
        for event_id, url in targets.items():
            self._result(url, event_id)
        if self.history is not None:
            self.backfill()

    def backfill(self) -> None:
        """Completed events in data/ the store lacks (`complete_events`), up to BACKFILL_PER_RUN, oldest first.
        They're not on this run's cards, so one that 404s or has a bout without an outcome is skipped with a
        warning (left out of the store and data/) instead of failing every run, and tried again only after
        BACKFILL_RETRY (`backfill_skips`), so it doesn't take a slot every run. Their fighters are fetched only
        when fighters.json lacks them."""
        candidates = [e.id for e in sorted(self.existing.values(), key=lambda e: e.date)
                      if e.status == "completed" and e.id not in self.scraped]
        have = complete_events(self.history, candidates)
        waiting = {eid for (eid,) in self.history.execute(
            "SELECT event_id FROM backfill_skips WHERE tried_at > ?", (iso(self.now - BACKFILL_RETRY),))}
        todo = [eid for eid in candidates if eid not in have and eid not in waiting]
        if waiting & set(candidates):
            log.info("backfill: %d skipped events wait to be retried", len(waiting & set(candidates)))
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
                self._skip(event_id, str(exc))
                continue
            no_outcome = without_outcome(self.parsed[event_id])
            if no_outcome:
                self._skip(event_id, str(MissingOutcome(no_outcome)))
                self._forget(url, event_id)
                self.scraped.pop(event_id)
                continue
            self.backfilled.add(event_id)

    def _skip(self, event_id: str, why: str) -> None:
        log.warning("backfill: skipping event %s (retried after %d days): %s", event_id, BACKFILL_RETRY.days, why)
        self.skipped[event_id] = why

    def _result(self, url: str, event_id: str) -> Event:
        event = self._detail(url, event_id)
        if event_id in self.completed and not event.fights:  # all cancelled: the listing says it took place
            event = event.model_copy(update={"status": "completed"})
        prev = self.existing.get(event_id)
        if event.status == "completed" and (prev is None or prev.status != "completed"):
            self.refresh |= {fid for f in event.fights for fid in (f.red, f.blue)}
        if event.date >= self.cutoff:
            # a live run may have completed the card first: refresh whoever has fought since their page was fetched
            for fid in (fid for f in event.fights if f.result is not None for fid in (f.red, f.blue)):
                current = self.fighters.get(fid)
                if current and current.recent_fights is not None and \
                        not any(p.date == event.date for p in current.recent_fights):
                    self.refresh.add(fid)
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
        for url, html in self.pages.items():  # pages skipped for the store were taken out by `_forget`
            record_page(self.history, url, page_path(url), html.encode("utf-8"), stamp)
        for event_id in sorted(self.parsed):
            upsert_card(self.history, self.parsed[event_id], self.listed, self.completed)
        for fid in sorted(self.fighter_pages):
            upsert_fighter_page(self.history, self.fighter_pages[fid], stamp)
        self.history.execute("DELETE FROM backfill_skips WHERE event_id IN (SELECT value FROM json_each(?))",
                             (json.dumps(sorted(self.backfilled)),))
        self.history.executemany(
            "INSERT INTO backfill_skips (event_id, tried_at, reason) VALUES (?, ?, ?) "
            "ON CONFLICT (event_id) DO UPDATE SET tried_at = excluded.tried_at, reason = excluded.reason",
            [(eid, stamp, why) for eid, why in sorted(self.skipped.items())])

    def _write_data(self) -> dict[str, int]:
        merged = {eid: merge_event(self.existing.get(eid), e) for eid, e in self.scraped.items()}
        events = {**self.existing, **merged}
        old_index = self.store.load_index()
        # ufcstats takes a card off its upcoming listing once it starts, before results are on its page: keep it
        # (and should_run's hourly runs, which only look at indexed events) until it's completed or RESULTS_GRACE
        cutoff = (self.now.date() - RESULTS_GRACE).isoformat()
        listed = set(self.upcoming_ids)
        awaiting = sorted((e for e in events.values()
                           if e.status == "upcoming" and e.id not in listed and cutoff <= e.date <= self.today),
                          key=lambda e: e.date)
        index = Index(
            generated_at=iso(self.now),
            upcoming=[e.id for e in awaiting] + [eid for eid in self.upcoming_ids if events[eid].date >= cutoff],
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
        for eid, event in merged.items():
            if eid not in self.backfilled and event.date >= self.cutoff:
                log_seen(self.existing.get(eid), event, self.now)
        return {
            "events_written": len(merged),
            "upcoming": len(index.upcoming),
            "completed": len(index.recent),
            "fights": sum(len(e.fights) for e in merged.values()),
            "fighters": len(self.fighters),
            "requests": getattr(self.client, "requests", 0),
        }


def _store_live(history: sqlite3.Connection, pages: list[tuple[str, str, ParsedEvent]], now: datetime) -> None:
    """The live events' pages into the store, in one transaction, as `_Run.write` does. Each event keeps the store's
    `source` (whether the completed listing had it), which a live run, without the listing, can't tell."""
    history.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
    history.execute("BEGIN IMMEDIATE")
    try:
        for url, html, parsed in pages:
            record_page(history, url, page_path(url), html.encode("utf-8"), iso(now))
            row = history.execute("SELECT source FROM events WHERE event_id = ?", (parsed.event.id,)).fetchone()
            upsert_card(history, parsed, {parsed.event.id} if row and row[0] == "listing" else set(), set())
        history.commit()
    except BaseException:
        history.rollback()
        raise


def scrape_live(store: Store, client, now: datetime, history: sqlite3.Connection | None = None) -> int:
    """`fightcard scrape --live`: see the module docstring. 0 when something was written, live.UNCHANGED when
    nothing was, 1 on a failure (nothing written)."""
    try:
        due = live.due_events(store.root, now.date())
    except ValueError as exc:
        print(f"scrape failed, nothing written: {exc}", file=sys.stderr)
        return 1
    if not due:
        print("scrape live: no live event, nothing fetched", file=sys.stderr)
        return live.UNCHANGED
    existing = store.load_events()
    changed: dict[str, Event] = {}
    pages: list[tuple[str, str, ParsedEvent]] = []
    try:
        for event_id in due:
            url = event_url(event_id)
            html = client.get(url)
            parsed = hold_unresulted(parse_event_detail(html, event_id))
            old = existing[event_id]
            if not parsed.event.fights:
                log.warning("live: event %s's page has no bouts; left for the hourly run", event_id)
                continue
            # the card's name, date and place stay as the hourly run wrote them (from the listing)
            new = merge_event(old, old.model_copy(update={"fights": parsed.event.fights,
                                                          "status": parsed.event.status}))
            if without_ratings(new) != without_ratings(old):
                changed[event_id] = new
                pages.append((url, html, parsed))
        if not changed:
            print(f"scrape live: unchanged ({', '.join(due)}), nothing written", file=sys.stderr)
            return live.UNCHANGED
        if history is not None:
            _store_live(history, pages, now)
    except (Halt, HttpError, sqlite3.Error) as exc:
        print(f"scrape failed, nothing written: {exc}", file=sys.stderr)
        return 1
    try:
        _write_live(store, existing, changed, pages, now)
    except OSError as exc:
        print(f"scrape failed: {DataWriteFailed(exc, store_updated=history is not None)}", file=sys.stderr)
        return 1
    for event_id, event in changed.items():
        log_seen(existing[event_id], event, now)
    results = sum(f.result is not None for e in changed.values() for f in e.fights)
    print(f"scrape live ok: events_written={len(changed)}, results={results}, "
          f"requests={getattr(client, 'requests', len(due))}", file=sys.stderr)
    return 0


def _write_live(store: Store, existing: dict[str, Event], changed: dict[str, Event],
                pages: list[tuple[str, str, ParsedEvent]], now: datetime) -> None:
    events = {**existing, **changed}
    fighters = store.load_fighters()
    stubs = {fid: Fighter(id=fid, name=parsed.fighters.get(fid) or fid)
             for _, _, parsed in pages for f in changed[parsed.event.id].fights for fid in (f.red, f.blue)
             if fid not in fighters}
    old = store.load_index()
    index = Index(
        generated_at=iso(now),
        # an id with no event file (an earlier partial write) is dropped, as the hourly run's index drops it
        upcoming=[eid for eid in old.upcoming if eid in events and events[eid].status != "completed"],
        recent=[e.id for e in sorted((e for e in events.values() if e.status == "completed"),
                                     key=lambda e: e.date, reverse=True)],
        model=old.model,
    )
    for event in changed.values():
        store.save_event(event)
    if stubs:
        store.save_fighters({**fighters, **stubs})
    store.save_index(index)


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
