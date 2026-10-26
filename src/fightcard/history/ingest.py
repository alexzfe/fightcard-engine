"""Load a ufcstats HTML cache into the history store with the site's own parsers.

The cache layout is fight-auto-research's `data/raw_html/ufcstats/`:
  listings/events_completed.html   required: says which events are UFC events (`store.is_ufc_event`)
  event-details/<event_id>.html    events and their cards, results included
  fighter-details/<fighter_id>.html  optional: bio and record
  _fetch_log.jsonl                 optional: {"url", "path", "fetched_at" (epoch s), ...} per fetch; the last wins
Everything goes in one transaction: a page that fails to parse leaves the store as it was, and so does a completed
bout with a method but no W/L outcome (`MissingOutcome`; it would drop out of the rating silently).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fightcard.history.store import is_ufc_event, upsert_event, upsert_fighter, upsert_page
from fightcard.models import Fighter
from fightcard.ufcstats.parsers import ParsedEvent, parse_event_detail, parse_event_list, parse_fighter

LISTING = Path("listings") / "events_completed.html"
BASE_URL = "http://ufcstats.com"
LISTING_URL = f"{BASE_URL}/statistics/events/completed?page=all"


class MissingOutcome(ValueError):
    def __init__(self, fight_ids: list[str]):
        self.fight_ids = sorted(fight_ids)
        super().__init__(f"completed bouts with a method but no W/L outcome: {', '.join(self.fight_ids)}")


def _fetch_log(html_dir: Path) -> dict[str, tuple[str, str]]:
    """Page path relative to the cache ("event-details/<id>.html") -> (url, fetched_at ISO 8601 UTC)."""
    log = html_dir / "_fetch_log.jsonl"
    if not log.is_file():
        return {}
    out = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            rel = "/".join(Path(entry["path"]).parts[-2:])
            stamp = datetime.fromtimestamp(entry["fetched_at"], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out[rel] = (entry["url"], stamp)
    return out


# --- one row per parsed page; `fightcard scrape --history-db` uses these too --------------------------------------


def page_path(url: str) -> str:
    """Where a ufcstats page sits in the cache: listings/events_completed.html, event-details/<id>.html, ..."""
    if url == LISTING_URL:
        return LISTING.as_posix()
    kind, page_id = url.rstrip("/").rsplit("/", 2)[-2:]
    return f"{kind}/{page_id}.html"


def record_page(conn: sqlite3.Connection, url: str, rel: str, data: bytes, fetched_at: str | None) -> None:
    upsert_page(conn, url, rel, hashlib.sha256(data).hexdigest(), fetched_at)


def listed_ids(listing_html: str) -> set[str]:
    """The event ids in ufcstats' completed-events listing (`store.is_ufc_event`)."""
    return {stub.id for stub in parse_event_list(listing_html)}


def completed_ids(listing_html: str) -> set[str]:
    """The listing's completed events: every row but the next upcoming event's (next.png)."""
    return {stub.id for stub in parse_event_list(listing_html) if not stub.next}


def upsert_card(conn: sqlite3.Connection, parsed: ParsedEvent, listed: set[str], completed: set[str]) -> None:
    """An event page: the event, its card, and the fighters' names as the card spells them. A card with no bouts
    (all cancelled) parses as upcoming; in `completed` (`completed_ids`) it's stored completed. A listed card with
    bouts keeps its parsed status: completed once a result is on the page."""
    ev = parsed.event
    if ev.id in completed and not ev.fights:
        ev = ev.model_copy(update={"status": "completed"})
    upsert_event(conn, ev, is_ufc_event(ev.name, ev.id in listed), "listing" if ev.id in listed else "history")
    for fid, name in parsed.fighters.items():
        upsert_fighter(conn, fid, name)


def without_outcome(parsed: ParsedEvent) -> list[str]:
    """Fight ids on the page with a method but no W/L outcome (`MissingOutcome`)."""
    return [f.id for f in parsed.event.fights if f.result is not None and f.result.outcome is None]


def upsert_fighter_page(conn: sqlite3.Connection, fighter: Fighter, fetched_at: str | None) -> None:
    upsert_fighter(conn, fighter.id, fighter.name, fighter, fetched_at)


class _Pages:
    def __init__(self, conn: sqlite3.Connection, html_dir: Path):
        self.conn, self.html_dir, self.log, self.count = conn, html_dir, _fetch_log(html_dir), 0

    def read(self, path: Path, default_url: str) -> tuple[str, str | None]:
        """The page's text and fetch time; records it in `pages`."""
        data = path.read_bytes()
        rel = path.relative_to(self.html_dir).as_posix()
        url, fetched_at = self.log.get(rel, (default_url, None))
        record_page(self.conn, url, rel, data, fetched_at)
        self.count += 1
        return data.decode("utf-8", errors="replace"), fetched_at


def ingest(conn: sqlite3.Connection, html_dir: Path) -> dict[str, int]:
    """Upsert every cached page; returns how many events, fights, fighters and pages were read."""
    html_dir = Path(html_dir)
    if not (html_dir / LISTING).is_file():
        raise FileNotFoundError(f"{html_dir / LISTING} is missing; it says which events are UFC events")
    counts = {"events": 0, "fights": 0, "fighters": 0}
    with conn:
        pages = _Pages(conn, html_dir)
        listing, _ = pages.read(html_dir / LISTING, LISTING_URL)
        listed, completed = listed_ids(listing), completed_ids(listing)
        seen: set[str] = set()
        no_outcome: list[str] = []
        for path in sorted((html_dir / "event-details").glob("*.html")):
            html, _ = pages.read(path, f"{BASE_URL}/event-details/{path.stem}")
            parsed = parse_event_detail(html, path.stem)
            upsert_card(conn, parsed, listed, completed)
            seen |= parsed.fighters.keys()
            counts["events"] += 1
            counts["fights"] += len(parsed.event.fights)
            no_outcome += without_outcome(parsed)
        if no_outcome:
            raise MissingOutcome(no_outcome)
        for path in sorted((html_dir / "fighter-details").glob("*.html")):
            html, fetched_at = pages.read(path, f"{BASE_URL}/fighter-details/{path.stem}")
            fighter = parse_fighter(html, path.stem)
            upsert_fighter_page(conn, fighter, fetched_at)
            seen.add(fighter.id)
        counts["fighters"], counts["pages"] = len(seen), pages.count
    return counts
