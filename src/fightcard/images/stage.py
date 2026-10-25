"""`fightcard images`: download headshots for fighters on upcoming cards.

A lookup miss never fails the pipeline; only unreadable data/ does.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fightcard.images.download import validate_image
from fightcard.images.http import HttpClient
from fightcard.images.misses import Misses
from fightcard.images.sources import ImageFinder
from fightcard.models import Event, Fighter
from fightcard.store import Store

log = logging.getLogger(__name__)


class DataError(Exception):
    """data/ is missing or unreadable."""


@dataclass
class Summary:
    hits: int = 0
    misses: int = 0
    skipped: int = 0  # missed within the retry window


def make_client() -> HttpClient:
    return HttpClient(min_interval=1.0)


def _load(store: Store) -> tuple[dict[str, Fighter], list[Event]]:
    if not store.root.is_dir():
        raise DataError(f"{store.root} is not a directory")
    try:
        return store.load_fighters(), list(store.load_events().values())
    except (OSError, ValueError, AttributeError) as e:
        raise DataError(f"cannot read {store.root}: {e}") from e


def _upcoming_fighter_ids(events: list[Event]) -> list[str]:
    upcoming = sorted((e for e in events if e.status == "upcoming"), key=lambda e: e.date)
    seen: dict[str, None] = {}
    for event in upcoming:
        for fight in event.fights:
            seen.setdefault(fight.red)
            seen.setdefault(fight.blue)
    return list(seen)


def _download_first(client: HttpClient, urls: Iterable[str]) -> tuple[bytes, str] | None:
    """Body and extension of the first URL that yields a real image."""
    for url in urls:
        resp = client.get(url)
        ext = validate_image(resp.body) if resp.ok else None
        if ext:
            return resp.body, ext
    return None


def process(
    data_dir: Path, client: HttpClient, now: datetime, limit: int | None = None
) -> Summary:
    store = Store(data_dir)
    fighters, events = _load(store)
    fighter_ids = _upcoming_fighter_ids(events)
    images_dir = data_dir / "images"
    misses = Misses.load(images_dir / "misses.json")
    finder = ImageFinder(client)
    summary = Summary()

    for fid in fighter_ids:
        fighter = fighters.get(fid)
        if fighter is None or fighter.image:
            continue
        if misses.recently_tried(fid, now):
            summary.skipped += 1
            continue
        if limit is not None and summary.hits + summary.misses >= limit:
            break
        found = _download_first(client, finder.candidates(fighter.name, fighter.nickname))
        if found is None:
            misses.record(fid, now)
            summary.misses += 1
            log.info("image miss %s (%s)", fighter.name, fid)
            continue
        body, ext = found
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / f"{fid}.{ext}").write_bytes(body)
        fighter.image = f"images/{fid}.{ext}"
        misses.clear(fid)
        summary.hits += 1
        log.info("image hit %s (%s)", fighter.name, fid)

    if summary.hits:
        store.save_fighters(fighters)
    if summary.hits or summary.misses:
        misses.save()
    return summary


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=None, help="max fighters to look up")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        summary = process(Path(args.data), make_client(), datetime.now(UTC), args.limit)
    except DataError as e:
        print(f"images: {e}", file=sys.stderr)
        return 1
    print(f"images: {summary.hits} hits, {summary.misses} misses, {summary.skipped} skipped")
    return 0
