"""Form at fight time: a fighter's last results before a day, for `Fight.form` (docs/CONTRACT.md, "1.4").

It reads every bout with a result in the history store, whatever the rating's scope (`rating_bouts` leaves small
non-UFC cards out; a fighter's form counts them, as their ufcstats page does). "Before" is strictly before the day,
so a card's own bouts never count toward its form; within an earlier day (an early-UFC tournament) the bouts come
main event first, the order they ended in reverse.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from fightcard.history.store import names
from fightcard.models import PastFight

FORM_SIZE = 3


class Form:
    """Each fighter's bouts with a result, newest first, from their side."""

    def __init__(self, bouts: dict[str, list[PastFight]], days: dict[str, str]):
        self._bouts = bouts
        self._days = days

    @classmethod
    def from_store(cls, conn: sqlite3.Connection) -> Form:
        who = names(conn)
        rows = conn.execute(
            "SELECT f.fight_id, e.date, e.name, f.red, f.blue, f.outcome, f.winner, f.method, f.round, f.time "
            "FROM fights f JOIN events e USING (event_id) "
            "WHERE f.outcome IS NOT NULL AND e.date != '' AND (f.outcome != 'win' OR f.winner IS NOT NULL) "
            "ORDER BY e.date DESC, f.position, f.fight_id")
        bouts: dict[str, list[PastFight]] = defaultdict(list)
        days = {}
        for fight_id, day, event, red, blue, outcome, winner, method, rnd, time in rows:
            days[fight_id] = day
            for me, them in ((red, blue), (blue, red)):
                result = ("win" if winner == me else "loss") if outcome == "win" else outcome
                bouts[me].append(PastFight(result=result, opponent=who.get(them, them), opponent_id=them,
                                           method=method, round=rnd, time=time, event=event, date=day))
        return cls(dict(bouts), days)

    def day(self, fight_id: str) -> str | None:
        """The store's date for a bout with a result, or None when the store doesn't have it."""
        return self._days.get(fight_id)

    def before(self, fighter_id: str, day: str) -> list[PastFight]:
        """The last FORM_SIZE results strictly before `day` (YYYY-MM-DD), newest first; [] for a debut or a fighter
        the store doesn't know."""
        earlier = (b for b in self._bouts.get(fighter_id, ()) if b.date < day)
        return [b for _, b in zip(range(FORM_SIZE), earlier, strict=False)]
