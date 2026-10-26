"""ufcstats HTML parsers (BeautifulSoup).

Ported from finish-finder `scraper/ufc_scraper/parsers.py` (parse_event_list,
parse_event_detail, parse_fight_outcome) and trimmed to the fields in
fightcard.models. Selectors confirmed against live HTML on 2026-10-24.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup, SoupStrainer, Tag

from fightcard.models import Event, Fight, Fighter, PastFight, Record, Result

_DETAIL_ID = re.compile(r"/(?:event|fight|fighter)-details/([0-9a-fA-F]+)")
_RECORD = re.compile(r"(\d+)-(\d+)-(\d+)(?:\s*\((\d+)\s*NC\))?")
_HEIGHT = re.compile(r"(\d+)'\s*(\d+)\"")
_INCHES = re.compile(r"(\d+(?:\.\d+)?)\"")
_HISTORY_DATE = re.compile(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})")
RECENT_FIGHTS = 3
# Fighter pages are mostly fight-history tables; build only the header, info boxes and table body.
_FIGHTER_PARTS = SoupStrainer(
    ["h2", "p", "ul", "tbody"],
    class_=["b-content__title", "b-content__Nickname", "b-list__box-list", "b-fight-details__table-body"],
)


@dataclass(frozen=True)
class EventStub:
    """One row of the upcoming or completed events listing."""

    id: str
    name: str
    date: str | None
    location: str | None
    url: str


@dataclass(frozen=True)
class ParsedEvent:
    event: Event
    fighters: dict[str, str]  # fighter id -> name as shown on the card


# --- helpers ----------------------------------------------------------------


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def extract_id(url: str | None) -> str | None:
    m = _DETAIL_ID.search(url or "")
    return m[1] if m else None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_record(text: str) -> Record:
    m = _RECORD.search(text or "")
    if not m:
        return Record()
    return Record(w=int(m[1]), l=int(m[2]), d=int(m[3]), nc=int(m[4] or 0))


def parse_history_date(text: str) -> str | None:
    """Fight-history dates: "Apr. 04, 2026", "Sept. 14, 2024", "May 18, 2024"."""
    m = _HISTORY_DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def parse_height(text: str) -> float | None:
    m = _HEIGHT.search(text or "")
    return float(int(m[1]) * 12 + int(m[2])) if m else None


def parse_reach(text: str) -> float | None:
    m = _INCHES.search(text or "")
    return float(m[1]) if m else None


# --- event lists --------------------------------------------------------------


def parse_event_list(html: str) -> list[EventStub]:
    """Rows of /statistics/events/upcoming or /completed, in page order.

    The completed listing's first row can be the next upcoming event (next.png);
    callers filter by date.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="b-statistics__table-events")
    if not table or not table.tbody:
        return []

    events = []
    for row in table.tbody.find_all("tr"):
        link = row.find("a", href=True)
        event_id = extract_id(link["href"]) if link else None
        if not event_id:
            continue
        cols = row.find_all("td")
        date_span = row.find("span", class_="b-statistics__date")
        events.append(
            EventStub(
                id=event_id,
                name=_text(link),
                date=parse_date(_text(date_span)) if date_span else None,
                location=(_text(cols[1]) or None) if len(cols) > 1 else None,
                url=link["href"].strip(),
            )
        )
    return events


# --- event detail ---------------------------------------------------------------


def _first_p(cell: Tag) -> str:
    return _text(cell.find("p") or cell)


_OUTCOME = {"win": "win", "loss": "win", "draw": "draw", "nc": "nc"}  # W/L flag -> Result.outcome


def _parse_result(cols: list[Tag], red: str, blue: str) -> Result | None:
    """Outcome columns: 0 W/L flag, 7 method (first <p>), 8 round, 9 time."""
    if len(cols) < 10 or not _first_p(cols[7]):
        return None
    flag = _text(cols[0].find("i", class_="b-flag__text")).lower()
    winner = {"win": red, "loss": blue}.get(flag)
    round_text = _first_p(cols[8])
    return Result(
        winner=winner,
        outcome=_OUTCOME.get(flag),
        method=_first_p(cols[7]),
        round=int(round_text) if round_text.isdigit() else None,
        time=_first_p(cols[9]) or None,
    )


def parse_event_detail(html: str, event_id: str) -> ParsedEvent:
    """An /event-details/<id> page. Status is "completed" iff any fight has a result."""
    soup = BeautifulSoup(html, "lxml")

    title = soup.find("h2", class_="b-content__title")
    highlight = title.find("span", class_="b-content__title-highlight") if title else None
    name = _text(highlight or title) or event_id

    date = location = None
    for item in soup.select("ul.b-list__box-list li.b-list__box-list-item"):
        text = _text(item)
        if text.startswith("Date:"):
            date = parse_date(text.removeprefix("Date:"))
        elif text.startswith("Location:"):
            location = text.removeprefix("Location:").strip() or None

    fights: list[Fight] = []
    fighters: dict[str, str] = {}
    for row in soup.select("tbody.b-fight-details__table-body tr.b-fight-details__table-row"):
        links = row.find_all("a", href=lambda h: h and "fighter-details" in h)
        if len(links) < 2:
            continue
        red, blue = extract_id(links[0]["href"]), extract_id(links[1]["href"])
        fighters.setdefault(red, _text(links[0]))
        fighters.setdefault(blue, _text(links[1]))

        cols = row.find_all("td")
        weight_cell = cols[6] if len(cols) > 6 else None
        fights.append(
            Fight(
                id=extract_id(row.get("data-link")) or f"{event_id}-{len(fights) + 1}",
                weight_class=(_first_p(weight_cell) or None) if weight_cell else None,
                title_fight=bool(weight_cell and weight_cell.find("img", src=re.compile("belt"))),
                red=red,
                blue=blue,
                result=_parse_result(cols, red, blue),
            )
        )

    status = "completed" if any(f.result for f in fights) else "upcoming"
    event = Event(
        id=event_id,
        slug=slugify(name),
        name=name,
        date=date or "",
        location=location,
        status=status,
        fights=fights,
    )
    return ParsedEvent(event=event, fighters=fighters)


# --- fighter page -----------------------------------------------------------------


def parse_fighter(html: str, fighter_id: str) -> Fighter:
    """A /fighter-details/<id> page: name, nickname, record, height, reach, stance, DOB."""
    soup = BeautifulSoup(html, "lxml", parse_only=_FIGHTER_PARTS)
    info: dict[str, str] = {}
    for item in soup.select("li.b-list__box-list-item"):
        label = item.find("i", class_="b-list__box-item-title")
        if label:
            key = _text(label)
            info.setdefault(key.rstrip(":").upper(), _text(item).removeprefix(key).strip())

    def value(key: str) -> str | None:
        v = info.get(key, "")
        return v if v and v != "--" else None

    dob = value("DOB")
    return Fighter(
        recent_fights=_parse_history(soup, fighter_id),
        id=fighter_id,
        name=_text(soup.find("span", class_="b-content__title-highlight")),
        nickname=_text(soup.find("p", class_="b-content__Nickname")) or None,
        record=parse_record(_text(soup.find("span", class_="b-content__title-record"))),
        height_in=parse_height(value("HEIGHT") or ""),
        reach_in=parse_reach(value("REACH") or ""),
        stance=value("STANCE"),
        dob=parse_date(dob) if dob else None,
    )


def _parse_history(soup: BeautifulSoup, fighter_id: str) -> list[PastFight]:
    """Completed bouts, newest first: 0 flag, 1 fighters (opponent second), 6 event + date,
    7 method, 8 round, 9 time. The "next" row (upcoming bout) is skipped."""
    fights: list[PastFight] = []
    for row in soup.select("tbody.b-fight-details__table-body tr.b-fight-details__table-row"):
        cols = row.find_all("td", recursive=False)
        flag = _text(row.find("i", class_="b-flag__text")).lower()
        if flag not in ("win", "loss", "draw", "nc") or len(cols) < 10:
            continue
        links = cols[1].find_all("a", href=lambda h: h and "fighter-details" in h)
        opponent = next((a for a in links if extract_id(a["href"]) != fighter_id), None)
        event_ps = cols[6].find_all("p")
        round_text = _first_p(cols[8])
        fights.append(
            PastFight(
                result=flag,
                opponent=_text(opponent) or "Unknown",
                opponent_id=extract_id(opponent["href"]) if opponent else None,
                method=_first_p(cols[7]) or None,
                round=int(round_text) if round_text.isdigit() else None,
                time=_first_p(cols[9]) or None,
                event=_text(event_ps[0]) if event_ps else "",
                date=parse_history_date(_text(event_ps[-1])) if event_ps else None,
            )
        )
        if len(fights) == RECENT_FIGHTS:
            break
    return fights
