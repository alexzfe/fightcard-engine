"""`fightcard scrape`: ufcstats → data/events/*.json, fighters.json, index.json.

Everything is fetched and validated in memory first; nothing is written unless
the whole run succeeds (non-zero exit = do not commit, do not deploy).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fightcard.models import Event, Fighter, Index
from fightcard.store import Store
from fightcard.ufcstats.client import Client, Halt, HttpError
from fightcard.ufcstats.parsers import (
    parse_event_detail,
    parse_event_list,
    parse_fighter,
    slugify,
)

log = logging.getLogger(__name__)

BASE_URL = "http://ufcstats.com"
UPCOMING_URL = f"{BASE_URL}/statistics/events/upcoming"
COMPLETED_URL = f"{BASE_URL}/statistics/events/completed"
FIGHTER_MAX_AGE = timedelta(days=30)


class ValidationError(Exception):
    pass


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


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return scrape(Store(args.data), make_client(args.cookie_file), now=utcnow(), recent=args.recent)


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
    def __init__(self, store: Store, client, now: datetime, recent: int):
        self.store = store
        self.client = client
        self.now = now
        self.today = now.date().isoformat()
        self.recent = recent
        self.existing = store.load_events()
        self.fighters = store.load_fighters()
        self.scraped: dict[str, Event] = {}
        self.card_names: dict[str, str] = {}
        self.refresh: set[str] = set()  # fighters whose record just changed
        self.upcoming_ids: list[str] = []

    def _detail(self, url: str, event_id: str) -> Event:
        parsed = parse_event_detail(self.client.get(url), event_id)
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
                # far-out cards often have no bouts yet: don't write or index them
                log.info("skipping event %s (%s): 0 fights announced", stub.id, stub.name)
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
        if self.recent > 0:
            past = [s for s in parse_event_list(self.client.get(COMPLETED_URL))
                    if s.date and s.date < self.today]
            for stub in past[: self.recent]:
                targets.setdefault(stub.id, stub.url)

        for event_id, url in targets.items():
            event = self._detail(url, event_id)
            prev = self.existing.get(event_id)
            if event.status == "completed" and (prev is None or prev.status != "completed"):
                self.refresh |= {fid for f in event.fights for fid in (f.red, f.blue)}
            self.scraped[event_id] = event

    def fighter_details(self) -> None:
        needed = dict.fromkeys(fid for e in self.scraped.values() for f in e.fights for fid in (f.red, f.blue))
        for fid in needed:
            current = self.fighters.get(fid)
            if current and fid not in self.refresh and is_fresh(current, self.now):
                continue
            try:
                fetched = parse_fighter(self.client.get(fighter_url(fid)), fid)
            except HttpError as exc:
                log.warning("fighter %s: %s", fid, exc)
                if current is None:
                    self.fighters[fid] = Fighter(id=fid, name=self.card_names.get(fid, fid))
                continue
            self.fighters[fid] = fetched.model_copy(update={
                "name": fetched.name or self.card_names.get(fid, fid),
                "image": current.image if current else None,
                "details_fetched_at": iso(self.now),
            })

    def write(self) -> dict[str, int]:
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


def scrape(store: Store, client, now: datetime, recent: int = 1) -> int:
    run_ = _Run(store, client, now, recent)
    try:
        run_.upcoming()
        run_.results()
        run_.fighter_details()
    except (Halt, HttpError, ValidationError) as exc:
        print(f"scrape failed, nothing written: {exc}", file=sys.stderr)
        return 1
    counts = run_.write()
    print("scrape ok: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0
