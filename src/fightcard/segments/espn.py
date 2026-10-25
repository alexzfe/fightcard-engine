"""ESPN lookups: event ids from the scoreboard calendar, bouts by segment from fightcenter.

Everything here is defensive: ESPN's payloads are undocumented, so junk parses to
nothing rather than raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from rapidfuzz import fuzz

from fightcard.images.names import normalize_name
from fightcard.models import CardSegment, Event

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
FIGHTCENTER_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/mma/ufc/fightcenter/{event_id}"
)
FIGHTCENTER_PARAMS = {"region": "us", "lang": "en"}

_SEGMENTS: dict[str, CardSegment] = {
    "main": "main",
    "prelims1": "prelims",
    "prelims2": "early_prelims",
}
_EVENT_ID = re.compile(r"/events/(\d+)")
EVENT_NAME_THRESHOLD = 85  # rapidfuzz token_sort_ratio on the headliner part
MAX_DAYS_APART = 1  # ESPN dates are UTC; evening cards in the Americas land on the next day


@dataclass(frozen=True)
class CalendarEntry:
    espn_id: str
    label: str
    start: date  # UTC date of the first bout


@dataclass(frozen=True)
class Bout:
    segment: CardSegment
    names: tuple[str, str]


def segment_of(name: str | None) -> CardSegment | None:
    return _SEGMENTS.get(name or "")


def parse_calendar(data: Any) -> list[CalendarEntry]:
    try:
        calendar = data["leagues"][0]["calendar"]
    except (KeyError, IndexError, TypeError):
        return []
    entries = []
    for item in calendar if isinstance(calendar, list) else []:
        try:
            match = _EVENT_ID.search(item["event"]["$ref"])
            start = datetime.fromisoformat(item["startDate"]).date()
            label = str(item["label"])
        except (KeyError, TypeError, ValueError):
            continue
        if match:
            entries.append(CalendarEntry(match.group(1), label, start))
    return entries


def _headliner(name: str) -> str:
    """"UFC Fight Night: Procházka vs. Stirling" -> "prochazka vs stirling"."""
    return normalize_name(name.split(":", 1)[-1])


def match_event(event: Event, calendar: list[CalendarEntry]) -> str | None:
    """ESPN event id for a ufcstats event: within a day of its date and the same headliners."""
    day = date.fromisoformat(event.date)
    ours = _headliner(event.name)
    for entry in calendar:
        if abs((entry.start - day).days) > MAX_DAYS_APART:
            continue
        if fuzz.token_sort_ratio(ours, _headliner(entry.label)) >= EVENT_NAME_THRESHOLD:
            return entry.espn_id
    return None


def parse_card(data: Any) -> list[Bout]:
    cards = data.get("cards") if isinstance(data, dict) else None
    bouts = []
    for key, card in cards.items() if isinstance(cards, dict) else []:
        segment = segment_of(key)
        competitions = card.get("competitions") if isinstance(card, dict) else None
        if segment is None or not isinstance(competitions, list):
            continue
        for comp in competitions:
            try:
                names = tuple(c["athlete"]["displayName"] for c in comp["competitors"])
            except (KeyError, TypeError):
                continue
            if len(names) == 2 and all(isinstance(n, str) and n for n in names):
                bouts.append(Bout(segment, names))
    return bouts
