"""Load a ufcstats HTML cache into the history store with the site's own parsers.

The cache layout is fight-auto-research's `data/raw_html/ufcstats/`:
  listings/events_completed.html   required: says which events are UFC events (`store.is_ufc_event`)
  event-details/<event_id>.html    events and their cards, results included
  fighter-details/<fighter_id>.html  optional: bio and record
  _fetch_log.jsonl                 optional: {"url", "path", "fetched_at" (epoch s), ...} per fetch; the last wins
Everything goes in one transaction: a page that fails to parse leaves the store as it was.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fightcard.history.store import is_ufc_event, upsert_event, upsert_fighter, upsert_page
from fightcard.ufcstats.parsers import parse_event_detail, parse_event_list, parse_fighter

LISTING = Path("listings") / "events_completed.html"
BASE_URL = "http://ufcstats.com"
_LISTING_URL = f"{BASE_URL}/statistics/events/completed?page=all"


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


class _Pages:
    def __init__(self, conn: sqlite3.Connection, html_dir: Path):
        self.conn, self.html_dir, self.log, self.count = conn, html_dir, _fetch_log(html_dir), 0

    def read(self, path: Path, default_url: str) -> tuple[str, str | None]:
        """The page's text and fetch time; records it in `pages`."""
        data = path.read_bytes()
        rel = path.relative_to(self.html_dir).as_posix()
        url, fetched_at = self.log.get(rel, (default_url, None))
        upsert_page(self.conn, url, rel, hashlib.sha256(data).hexdigest(), fetched_at)
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
        listing, _ = pages.read(html_dir / LISTING, _LISTING_URL)
        listed = {stub.id for stub in parse_event_list(listing)}
        seen: set[str] = set()
        for path in sorted((html_dir / "event-details").glob("*.html")):
            html, _ = pages.read(path, f"{BASE_URL}/event-details/{path.stem}")
            parsed = parse_event_detail(html, path.stem)
            ev = parsed.event
            upsert_event(conn, ev, is_ufc_event(ev.name, ev.id in listed), "listing" if ev.id in listed else "history")
            for fid, name in parsed.fighters.items():
                upsert_fighter(conn, fid, name)
            seen |= parsed.fighters.keys()
            counts["events"] += 1
            counts["fights"] += len(ev.fights)
        for path in sorted((html_dir / "fighter-details").glob("*.html")):
            html, fetched_at = pages.read(path, f"{BASE_URL}/fighter-details/{path.stem}")
            fighter = parse_fighter(html, path.stem)
            upsert_fighter(conn, fighter.id, fighter.name, fighter, fetched_at)
            seen.add(fighter.id)
        counts["fighters"], counts["pages"] = len(seen), pages.count
    return counts
