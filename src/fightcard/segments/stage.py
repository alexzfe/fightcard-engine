"""`fightcard segments`: tag each upcoming fight as main card, prelims or early prelims.

Segments are cosmetic, so an ESPN failure never fails the pipeline: known
segments are kept and the stage exits 0. Only unreadable data/ exits 1.
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fightcard.images.http import HttpClient, Response
from fightcard.images.names import names_match
from fightcard.models import Event, Fight, Fighter
from fightcard.segments import espn
from fightcard.store import Store

log = logging.getLogger(__name__)


class DataError(Exception):
    """data/ is missing or unreadable."""


@dataclass
class Summary:
    events: int = 0  # upcoming events whose ESPN card was read
    fights: int = 0  # fights given a segment
    unmatched: int = 0  # fights on a read card that ESPN doesn't list


# site.api.espn.com answers 403 to a User-Agent containing a URL, so don't reuse images.http's.
USER_AGENT = "fightcard/0.1 (non-commercial fan site)"


def _transport(url: str, params: dict | None = None, timeout: float = 15.0) -> Response:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, b"")


def make_client() -> HttpClient:
    return HttpClient(transport=_transport, min_interval=1.0)


def _get_json(client: HttpClient, url: str, params: dict | None = None):
    resp = client.get(url, params)
    if not resp.ok:
        log.warning("segments: %s returned %s", url, resp.status or "a network error")
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("segments: %s returned invalid JSON", url)
        return None


def _is(fighter: Fighter, espn_name: str) -> bool:
    return names_match(fighter.name, espn_name, nickname=fighter.nickname)


def _segment_for(fight: Fight, fighters: dict[str, Fighter], bouts: list[espn.Bout]):
    """Segment of the ESPN bout with both fighters, in either corner order."""
    red, blue = fighters.get(fight.red), fighters.get(fight.blue)
    if red is None or blue is None:
        return None
    for bout in bouts:
        a, b = bout.names
        if (_is(red, a) and _is(blue, b)) or (_is(red, b) and _is(blue, a)):
            return bout.segment
    return None


def _apply(event: Event, fighters: dict[str, Fighter], bouts: list[espn.Bout], summary: Summary) -> Event:
    fights = []
    for fight in event.fights:
        segment = _segment_for(fight, fighters, bouts)
        if segment is None:
            summary.unmatched += 1
            red, blue = fighters.get(fight.red), fighters.get(fight.blue)
            log.info("segments: %s has no ESPN bout for %s vs %s", event.name,
                     red.name if red else fight.red, blue.name if blue else fight.blue)
        else:
            summary.fights += 1
        fights.append(fight.model_copy(update={"segment": segment}))
    return event.model_copy(update={"fights": fights})


def _load(store: Store) -> tuple[dict[str, Fighter], list[Event]]:
    if not store.root.is_dir():
        raise DataError(f"{store.root} is not a directory")
    try:
        return store.load_fighters(), list(store.load_events().values())
    except (OSError, ValueError, AttributeError) as e:
        raise DataError(f"cannot read {store.root}: {e}") from e


def process(data_dir: Path, client: HttpClient) -> Summary:
    store = Store(data_dir)
    fighters, events = _load(store)
    upcoming = sorted((e for e in events if e.status == "upcoming"), key=lambda e: e.date)
    summary = Summary()
    if not upcoming:
        return summary

    calendar = espn.parse_calendar(_get_json(client, espn.SCOREBOARD_URL))
    if not calendar:
        log.warning("segments: no ESPN calendar; keeping known segments")
        return summary

    for event in upcoming:
        espn_id = espn.match_event(event, calendar)
        if espn_id is None:
            log.warning("segments: no ESPN event for %s (%s)", event.name, event.date)
            continue
        url = espn.FIGHTCENTER_URL.format(event_id=espn_id)
        bouts = espn.parse_card(_get_json(client, url, espn.FIGHTCENTER_PARAMS))
        if not bouts:
            log.warning("segments: empty ESPN card for %s; keeping known segments", event.name)
            continue
        summary.events += 1
        updated = _apply(event, fighters, bouts, summary)
        if updated != event:
            store.save_event(updated)
    return summary


def add_arguments(parser: argparse.ArgumentParser) -> None:
    del parser  # no options


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        summary = process(Path(args.data), make_client())
    except DataError as e:
        print(f"segments: {e}", file=sys.stderr)
        return 1
    print(f"segments: {summary.events} cards read, {summary.fights} fights tagged, "
          f"{summary.unmatched} unmatched")
    return 0

