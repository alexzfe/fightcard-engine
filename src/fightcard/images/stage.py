"""`fightcard images`: download headshots for fighters on the cards the site shows, then for the P4P fighters.

The P4P ids are computed here from the history store (`--history-db`, default `<--data>/../history/history.sqlite`),
the way `score` computes data/rankings.json, as of the run date: a fighter who enters the P4P lists gets their headshot
in the same run, before `score` writes them. A P4P fighter on no card may be missing from fighters.json: they get
images/<id>.webp and no `Fighter.image`.

A lookup miss never fails the pipeline, nor does a missing or unusable history store (the cards are still fetched);
only unreadable data/ does.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fightcard.history.rankings import divisions, p4p
from fightcard.history.rating import history_pass
from fightcard.images.download import normalize_image
from fightcard.images.http import HttpClient
from fightcard.images.misses import Misses
from fightcard.images.sources import ImageFinder
from fightcard.models import Event, Fighter, Index
from fightcard.store import Store

log = logging.getLogger(__name__)

_LEGACY_SUFFIXES = {".png", ".jpg", ".jpeg"}


class DataError(Exception):
    """data/ is missing or unreadable."""


@dataclass
class Summary:
    hits: int = 0
    misses: int = 0
    skipped: int = 0  # missed within the retry window


def make_client() -> HttpClient:
    return HttpClient(min_interval=1.0)


def _load(store: Store) -> tuple[dict[str, Fighter], list[Event], Index | None]:
    if not store.root.is_dir():
        raise DataError(f"{store.root} is not a directory")
    try:
        return store.load_fighters(), list(store.load_events().values()), store.load_index()
    except (OSError, ValueError, AttributeError) as e:
        raise DataError(f"cannot read {store.root}: {e}") from e


def _fighter_ids(events: list[Event], index: Index | None) -> list[str]:
    """Fighters on the site's cards: upcoming soonest first, then recent results most recent first.

    A card first scraped after it happened was never upcoming, so recent results are looked up too.
    Older completed events (not in the index) are left alone. Without an index, upcoming only.
    """
    if index is None:
        cards = sorted((e for e in events if e.status == "upcoming"), key=lambda e: e.date)
    else:
        by_id = {e.id: e for e in events}
        cards = [by_id[eid] for eid in [*index.upcoming, *index.recent] if eid in by_id]
    seen: dict[str, None] = {}
    for event in cards:
        for fight in event.fights:
            seen.setdefault(fight.red)
            seen.setdefault(fight.blue)
    return list(seen)


def _p4p_names(db: Path, now: datetime) -> dict[str, str]:
    """The P4P fighters as of `now`'s date, id -> name, from the history store (read-only, like `score`; the same
    functions as data/rankings.json). A missing or unusable store means none: it only says whose headshots to fetch."""
    try:
        if not db.is_file():
            raise FileNotFoundError("no such file")
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            ratings, bouts, who, _ = history_pass(conn, now.date().isoformat())
        lists = p4p(ratings, who, divisions(bouts), now.date())
    except Exception as e:  # noqa: BLE001 - the store only says whose headshots to fetch; it never fails the step
        log.warning("no P4P headshots: history store %s is unusable (%s); fetching the card fighters only", db, e)
        return {}
    return {e["id"]: e["name"] for es in lists.values() for e in es}


def _write_headshot(path: Path, data: bytes) -> None:
    """Whole or not at all: a temp file beside it (a leading '.', so publish never takes it), then os.replace, as
    store.write_json does. A file at `path` is trusted as a good headshot, so a write that dies part-way must not leave one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _has_headshot(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0  # a 0-byte file is what a failed write leaves: no headshot


def _download_first(client: HttpClient, urls: Iterable[str]) -> bytes | None:
    """Normalised WebP of the first URL that yields a real image."""
    for url in urls:
        resp = client.get(url)
        webp = normalize_image(resp.body) if resp.ok else None
        if webp:
            return webp
    return None


def migrate_images(data_dir: Path) -> int:
    """Convert legacy png/jpg headshots in data/images to WebP in place; no network.

    Returns the number of files converted.
    """
    images_dir = data_dir / "images"
    legacy = sorted(p for p in images_dir.glob("*") if p.suffix.lower() in _LEGACY_SUFFIXES)
    if not legacy:
        return 0
    store = Store(data_dir)
    fighters, _, _ = _load(store)
    by_image = {f.image: f for f in fighters.values() if f.image}
    converted = 0
    for path in legacy:
        webp = normalize_image(path.read_bytes())
        if webp is None:
            log.warning("cannot convert %s; left as is", path.name)
            continue
        _write_headshot(images_dir / f"{path.stem}.webp", webp)
        path.unlink()
        fighter = by_image.get(f"images/{path.name}")
        if fighter is not None:
            fighter.image = f"images/{path.stem}.webp"
        converted += 1
    if converted:
        store.save_fighters(fighters)
    return converted


def process(
    data_dir: Path, client: HttpClient, now: datetime, limit: int | None = None, history_db: Path | None = None
) -> Summary:
    store = Store(data_dir)
    migrated = migrate_images(data_dir)
    if migrated:
        log.info("converted %d legacy headshots to webp", migrated)
    fighters, events, index = _load(store)
    fighter_ids = _fighter_ids(events, index)
    p4p_names = {} if history_db is None else _p4p_names(history_db, now)
    known = set(fighter_ids)
    fighter_ids += [fid for fid in p4p_names if fid not in known]  # after the cards
    images_dir = data_dir / "images"
    misses = Misses.load(images_dir / "misses.json")
    finder = ImageFinder(client)
    summary = Summary()
    linked = False

    for fid in fighter_ids:
        fighter = fighters.get(fid)
        if fighter is None and fid not in p4p_names:
            continue
        if fighter is not None and fighter.image:
            continue
        if _has_headshot(images_dir / f"{fid}.webp"):  # a P4P headshot saved before the fighter joined fighters.json
            if fighter is not None:
                fighter.image = f"images/{fid}.webp"
                linked = True
            continue
        if misses.recently_tried(fid, now):
            summary.skipped += 1
            continue
        if limit is not None and summary.hits + summary.misses >= limit:
            break
        name, nickname = (fighter.name, fighter.nickname) if fighter else (p4p_names[fid], None)
        found = _download_first(client, finder.candidates(name, nickname))
        if found is None:
            misses.record(fid, now)
            summary.misses += 1
            log.info("image miss %s (%s)", name, fid)
            continue
        _write_headshot(images_dir / f"{fid}.webp", found)
        if fighter:
            fighter.image = f"images/{fid}.webp"
        misses.clear(fid)
        summary.hits += 1
        log.info("image hit %s (%s)", name, fid)

    if summary.hits or linked:
        store.save_fighters(fighters)
    if summary.hits or summary.misses:
        misses.save()
    return summary


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=None, help="max fighters to look up")
    parser.add_argument("--history-db", type=Path, default=None,
                        help="bout-history store, for the P4P fighters' headshots "
                             "(default <--data>/../history/history.sqlite)")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        history_db = args.history_db or Path(args.data).resolve().parent / "history" / "history.sqlite"
        summary = process(Path(args.data), make_client(), datetime.now(UTC), args.limit, history_db)
    except DataError as e:
        print(f"images: {e}", file=sys.stderr)
        return 1
    print(f"images: {summary.hits} hits, {summary.misses} misses, {summary.skipped} skipped")
    return 0
