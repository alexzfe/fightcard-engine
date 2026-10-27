"""Shared data contract between pipeline stages and the Astro site.

See docs/CONTRACT.md. Changing a field here is a contract change: update the
doc and tell every stage owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True)  # data/rankings.json is machine-written: no "1" for 1


class P4PEntry(_Strict):
    rank: int = Field(ge=1)
    id: str  # ufcstats fighter id; the fighter may be on no card (not in fighters.json)
    name: str
    elo: int  # displayed scale, as Fighter.elo
    division: str | None  # weight class of the latest bout, Catch/Open Weight skipped; None = none
    image: str | None  # path relative to data/, e.g. "images/<id>.webp", of a published image; None = no headshot


class ActiveEntry(_Strict):
    id: str
    name: str
    elo: int
    division: str | None
    p4p_rank: int | None  # as Fighter.p4p_rank
    provisional: bool  # fewer than 3 UFC bouts


class P4P(_Strict):
    # PARAMS' p4p_men / p4p_women (history.rating imports this module, so they are copied; a test keeps them equal)
    men: list[P4PEntry] = Field(max_length=15)
    women: list[P4PEntry] = Field(max_length=10)

    @field_validator("men", "women")
    @classmethod
    def _ranked_in_order(cls, entries: list[P4PEntry]) -> list[P4PEntry]:
        if [e.rank for e in entries] != list(range(1, len(entries) + 1)):
            raise ValueError("ranks must be 1..n in order")
        return entries


class Rankings(_Strict):
    """data/rankings.json, written by `score` and published as /v1/rankings.json (docs/CONTRACT.md)."""

    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD the ratings are decayed to
    p4p: P4P
    # >= 1 UFC bout ever and any in-scope bout in the last 365 days; best Elo first (ties by id)
    active: list[ActiveEntry]

    @model_validator(mode="after")
    def _p4p_agrees_with_active(self) -> Rankings:
        """The lists and the index tell the same story: every P4P fighter is in `active` with the same rank, no other
        active row has one, and no id is in both lists, so a consumer can look an id up in `p4p.women` to tell the
        lists apart."""
        listed = [(e.id, e.rank) for es in (self.p4p.men, self.p4p.women) for e in es]
        ids = [fid for fid, _ in listed]
        if both := sorted({fid for fid in ids if ids.count(fid) > 1}):
            raise ValueError(f"P4P ids listed more than once: {', '.join(both)}")
        ranks = {a.id: a.p4p_rank for a in self.active}
        if off := [f"{fid} (rank {rank})" for fid, rank in listed if ranks.get(fid) != rank]:
            raise ValueError(f"P4P fighters missing from active or with another p4p_rank there: {', '.join(off)}")
        ranked = dict(listed)
        if stray := [f"{a.id} (p4p_rank {a.p4p_rank})" for a in self.active
                     if a.p4p_rank is not None and ranked.get(a.id) != a.p4p_rank]:
            raise ValueError(f"active rows with a p4p_rank that matches no P4P entry: {', '.join(stray)}")
        return self
