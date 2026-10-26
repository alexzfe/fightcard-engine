"""Shared data contract between pipeline stages and the Astro site.

See docs/CONTRACT.md. Changing a field here is a contract change: update the
doc and tell every stage owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FinishTier = Literal["high", "medium", "low"]
CardSegment = Literal["main", "prelims", "early_prelims"]
EventStatus = Literal["upcoming", "completed"]
PastResult = Literal["win", "loss", "draw", "nc"]
Outcome = Literal["win", "draw", "nc"]


class Record(BaseModel):
    w: int = 0
    l: int = 0
    d: int = 0
    nc: int = 0


class PastFight(BaseModel):
    """One completed bout from the fighter's ufcstats history, from their side."""

    result: PastResult
    opponent: str
    opponent_id: str | None = None
    method: str | None = None  # e.g. "KO/TKO", "SUB", "U-DEC"
    round: int | None = None
    time: str | None = None  # "4:12"
    event: str
    date: str | None = None  # YYYY-MM-DD


class BestWin(BaseModel):
    """The beaten opponent rated highest going into the bout (docs/ELO.md)."""

    opponent_id: str
    opponent: str
    elo: int  # opponent's rating going into that bout, on the displayed scale
    date: str  # YYYY-MM-DD


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
    recent_fights: list[PastFight] | None = None  # newest first, at most 3; None = not fetched yet
    # Rating (docs/ELO.md), all set by `score`. elo/elo_peak are on the displayed (anchored) scale, rounded.
    elo: int | None = None  # current rating, decayed to now; None = unknown
    elo_peak: int | None = None  # highest rating from the 5th UFC bout on; None = not reached / unknown
    p4p_rank: int | None = None  # 1-15 men, 1-10 women; None = unranked
    elo_provisional: bool = False  # fewer than 3 UFC bouts
    elo_inactive: bool = False  # no bout in the last 365 days
    best_win: BestWin | None = None  # shown under Stats; None = no win / unknown


class Result(BaseModel):
    winner: str | None = None  # fighter id; None for draw / no contest
    outcome: Outcome | None = None  # tells a draw from a no contest; None = not parsed (older data, no flag)
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
    segment: CardSegment | None = None  # from ESPN; None = unknown


class Event(BaseModel):
    id: str  # ufcstats event id
    slug: str  # URL slug, e.g. "ufc-fight-night-moicano-vs-nolan"
    name: str
    date: str  # YYYY-MM-DD
    location: str | None = None
    status: EventStatus
    fights: list[Fight]  # card order: main event first


class OosEvidence(BaseModel):
    bouts: int
    log_loss: float
    constant_log_loss: float  # a constant base-rate prediction, for comparison


class TierStats(BaseModel):
    bouts: int
    finish_rate: float
    mean_predicted: float


class ImportanceShare(BaseModel):
    group: str  # a theme key from fightcard.score.summary.GROUPS
    share: float  # % of the booster's total split gain, rounded to 0.1


class ModelInfo(BaseModel):
    as_of: str  # export date YYYY-MM-DD
    version: str
    # The export summary the how-it-works page quotes (fightcard.score.summary); None before `score` writes it.
    train_rows: int | None = None
    trees: int | None = None
    low_max: float | None = None
    medium_max: float | None = None
    oos: OosEvidence | None = None
    tiers: dict[Literal["low", "medium", "high"], TierStats] | None = None
    importance: list[ImportanceShare] | None = None
    # Fighters whose record moved since the export, so their bouts get no tier (`score`'s staleness guard); None
    # before `score` writes it. The model pull's research-side gate reads it.
    stale_fighters: int | None = None


class Index(BaseModel):
    generated_at: str  # ISO 8601 UTC
    upcoming: list[str]  # event ids, soonest first
    recent: list[str]  # completed event ids, most recent first
    model: ModelInfo | None = None
