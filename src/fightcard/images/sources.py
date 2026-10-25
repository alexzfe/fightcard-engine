"""Headshot lookup: ESPN scoreboard ids, then ESPN search, then Wikipedia page images."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from fightcard.images.http import HttpClient
from fightcard.images.names import names_match

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
ESPN_SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
HEADSHOT_URL = "https://a.espncdn.com/i/headshots/mma/players/full/{id}.png"


def headshot_url(athlete_id: str) -> str:
    return HEADSHOT_URL.format(id=athlete_id)


def parse_scoreboard(data: Any) -> list[tuple[str, str]]:
    """(display name, ESPN athlete id) for every competitor in the scoreboard response."""
    out = []
    for event in (data or {}).get("events", []):
        for comp in event.get("competitions", []):
            for c in comp.get("competitors", []):
                name = (c.get("athlete") or {}).get("displayName")
                if c.get("id") and name:
                    out.append((name, str(c["id"])))
    return out


def parse_espn_search(data: Any, name: str, nickname: str | None = None) -> str | None:
    for item in (data or {}).get("items", []):
        if item.get("sport") != "mma" or not item.get("id"):
            continue
        if names_match(name, item.get("displayName", ""), nickname=nickname):
            return str(item["id"])
    return None


def parse_wikipedia(data: Any, name: str) -> str | None:
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page_id, page in pages.items():
        if page_id == "-1" or "missing" in page:
            continue
        title = re.sub(r"\s*\(.*\)$", "", page.get("title", ""))
        if not names_match(name, title):
            continue
        for key in ("thumbnail", "original"):
            source = (page.get(key) or {}).get("source")
            if source:
                return source
    return None


def _json(resp) -> Any:
    if not resp.ok:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


class ImageFinder:
    """Yields candidate image URLs for a fighter, best source first, lazily."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._scoreboard: list[tuple[str, str]] | None = None

    def scoreboard(self) -> list[tuple[str, str]]:
        if self._scoreboard is None:
            self._scoreboard = parse_scoreboard(_json(self.client.get(ESPN_SCOREBOARD_URL)))
        return self._scoreboard

    def _espn_id(self, name: str, nickname: str | None) -> str | None:
        for display, athlete_id in self.scoreboard():
            if names_match(name, display, nickname=nickname):
                return athlete_id
        params = {"query": name, "limit": 5, "type": "player"}
        return parse_espn_search(_json(self.client.get(ESPN_SEARCH_URL, params)), name, nickname)

    def _wikipedia(self, name: str) -> str | None:
        params = {
            "action": "query",
            "titles": name,
            "prop": "pageimages",
            "piprop": "thumbnail|original",
            "pithumbsize": 600,
            "redirects": 1,
            "format": "json",
        }
        return parse_wikipedia(_json(self.client.get(WIKIPEDIA_API_URL, params)), name)

    def candidates(self, name: str, nickname: str | None = None) -> Iterator[str]:
        athlete_id = self._espn_id(name, nickname)
        if athlete_id:
            yield headshot_url(athlete_id)
        wiki = self._wikipedia(name)
        if wiki:
            yield wiki
