"""Shared data contract between pipeline stages and the Astro site.

See docs/CONTRACT.md. Changing a field here is a contract change: update the
doc and tell every stage owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FinishTier = Literal["high", "medium", "low"]
EventStatus = Literal["upcoming", "completed"]


class Record(BaseModel):
    w: int = 0
    l: int = 0
    d: int = 0
    nc: int = 0


class Fighter(BaseModel):
    id: str  # ufcstats fighter id (hex from fighter-details URL)
    name: str
    nickname: str | None = None
    record: Record = Field(default_factory=Record)
    height_in: float | None = None
    reach_in: float | None = None
    stance: str | None = None
    dob: str | None = None  # YYYY-MM-DD
    image: str | None = None  # path relative to data/, e.g. "images/<id>.png"
    details_fetched_at: str | None = None  # ISO 8601 UTC


class Result(BaseModel):
    winner: str | None = None  # fighter id; None for draw / no contest
    method: str | None = None  # e.g. "KO/TKO", "SUB", "U-DEC"
    round: int | None = None
    time: str | None = None  # "4:12"


class Fight(BaseModel):
    id: str  # ufcstats fight id when known, else "<event_id>-<n>"
    weight_class: str | None = None
    title_fight: bool = False
    red: str  # fighter id (left corner on ufcstats)
    blue: str  # fighter id
    finish_tier: FinishTier | None = None  # None renders as "—"
    tier_frozen_at: str | None = None  # ISO 8601; set once the event starts
    result: Result | None = None


class Event(BaseModel):
    id: str  # ufcstats event id
    slug: str  # URL slug, e.g. "ufc-fight-night-moicano-vs-nolan"
    name: str
    date: str  # YYYY-MM-DD
    location: str | None = None
    status: EventStatus
    fights: list[Fight]  # card order: main event first


class ModelInfo(BaseModel):
    as_of: str  # export date YYYY-MM-DD
    version: str


class Index(BaseModel):
    generated_at: str  # ISO 8601 UTC
    upcoming: list[str]  # event ids, soonest first
    recent: list[str]  # completed event ids, most recent first
    model: ModelInfo | None = None
