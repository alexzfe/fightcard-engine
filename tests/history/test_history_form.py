"""Form at fight time (`fightcard.history.form`): a fighter's last results before a day, from every bout with a result
in the history store, whatever the rating's scope (a small non-UFC card counts here though the rating leaves it out)."""

from __future__ import annotations

import pytest

from fightcard.history.form import Form
from fightcard.history.store import connect, upsert_event
from fightcard.models import Event, Fight, PastFight, Result

A, B, C, D, E = "a", "b", "c", "d", "e"
NAMES = {A: "Alan Able", B: "Ben Baker", C: "Cal Cole", D: "Dan Dore", E: "Eli Eads"}


def card(conn, eid: str, day: str, fights: list[Fight], ufc: bool = True) -> None:
    upsert_event(conn, Event(id=eid, slug=eid, name=f"Card {eid}", date=day, status="completed", fights=fights),
                 is_ufc=ufc, source="listing" if ufc else "history")


def bout(fid: str, red: str, blue: str, outcome: str | None = "win", winner: str | None = None,
         method: str | None = "U-DEC", rnd: int | None = 3, time: str | None = "5:00") -> Fight:
    result = None if outcome is None else Result(winner=winner if outcome == "win" else None, outcome=outcome,
                                                 method=method, round=rnd, time=time)
    return Fight(id=fid, red=red, blue=blue, result=result)


@pytest.fixture
def form(tmp_path) -> Form:
    """A: beat B (UFC, Jan), lost to C on a one-bout regional card (Feb, out of the rating's scope), drew with D
    (Mar), no contest with E (Apr), then fights B again in May. Plus a bout with no result yet (Jun)."""
    conn = connect(tmp_path / "history.sqlite")
    with conn:
        card(conn, "jan", "2026-01-10", [bout("f-jan", A, B, winner=A, method="KO/TKO", rnd=1, time="2:10")])
        card(conn, "feb", "2026-02-14", [bout("f-feb", C, A, winner=C, method="SUB", rnd=2, time="3:33")], ufc=False)
        card(conn, "mar", "2026-03-07", [bout("f-mar", A, D, outcome="draw", method="S-DEC")])
        card(conn, "apr", "2026-04-04", [bout("f-apr", E, A, outcome="nc", method="Overturned", rnd=2, time="1:01")])
        card(conn, "may", "2026-05-02", [bout("f-may", A, B, winner=B)])
        card(conn, "jun", "2026-06-06", [bout("f-jun", A, C, outcome=None)])
        for fid, name in NAMES.items():
            conn.execute("INSERT INTO fighters (fighter_id, name) VALUES (?, ?)", (fid, name))
    try:
        return Form.from_store(conn)
    finally:
        conn.close()


def test_the_last_three_results_before_the_day_newest_first_from_the_fighters_side(form):
    assert form.before(A, "2026-05-02") == [
        PastFight(result="nc", opponent="Eli Eads", opponent_id=E, method="Overturned", round=2, time="1:01",
                  event="Card apr", date="2026-04-04"),
        PastFight(result="draw", opponent="Dan Dore", opponent_id=D, method="S-DEC", round=3, time="5:00",
                  event="Card mar", date="2026-03-07"),
        PastFight(result="loss", opponent="Cal Cole", opponent_id=C, method="SUB", round=2, time="3:33",
                  event="Card feb", date="2026-02-14"),
    ]


def test_the_bout_on_the_day_and_after_it_are_not_counted(form):
    """Strictly before the day: the fight itself (and anything later) never counts toward its own form."""
    assert [f.date for f in form.before(A, "2026-03-07")] == ["2026-02-14", "2026-01-10"]
    assert [f.result for f in form.before(B, "2026-05-02")] == ["loss"]


def test_a_fighter_with_fewer_than_three_bouts_before_gets_what_there_is(form):
    assert form.before(A, "2026-02-14") == [
        PastFight(result="win", opponent="Ben Baker", opponent_id=B, method="KO/TKO", round=1, time="2:10",
                  event="Card jan", date="2026-01-10")]


def test_a_debut_or_an_unknown_fighter_has_no_form(form):
    assert form.before(A, "2026-01-10") == []
    assert form.before("nobody", "2026-12-31") == []


def test_a_bout_without_a_result_is_not_counted(form):
    assert form.before(C, "2026-12-31") == [
        PastFight(result="win", opponent="Alan Able", opponent_id=A, method="SUB", round=2, time="3:33",
                  event="Card feb", date="2026-02-14")]


def test_bouts_on_the_same_earlier_day_come_main_event_first(tmp_path):
    """An early-UFC tournament night: the final (the main event, first in card order) came after the semi."""
    conn = connect(tmp_path / "history.sqlite")
    with conn:
        card(conn, "t", "1993-11-12", [bout("final", A, B, winner=A), bout("semi", A, C, winner=A)])
        for fid, name in NAMES.items():
            conn.execute("INSERT INTO fighters (fighter_id, name) VALUES (?, ?)", (fid, name))
    form = Form.from_store(conn)
    conn.close()
    assert [f.opponent_id for f in form.before(A, "1994-03-11")] == [B, C]


def test_an_opponent_with_no_name_in_the_store_is_unknown_not_an_id(tmp_path):
    """As the ufcstats parser does: the site shows the name, so never a raw id."""
    conn = connect(tmp_path / "history.sqlite")
    with conn:
        card(conn, "jan", "2026-01-10", [bout("f-jan", A, "ghost", winner=A)])
        conn.execute("INSERT INTO fighters (fighter_id, name) VALUES (?, ?)", (A, NAMES[A]))
    form = Form.from_store(conn)
    conn.close()
    assert [(f.opponent, f.opponent_id) for f in form.before(A, "2026-12-31")] == [("Unknown", "ghost")]


def test_a_win_for_neither_corner_is_not_counted(tmp_path):
    """A malformed row (the winner is neither fighter) would read as a loss for both; it's left out instead."""
    conn = connect(tmp_path / "history.sqlite")
    with conn:
        card(conn, "jan", "2026-01-10", [bout("f-jan", A, B, winner=A)])
        card(conn, "feb", "2026-02-14", [bout("f-feb", A, B, winner=A)])
        conn.execute("UPDATE fights SET winner = 'zzz' WHERE fight_id = 'f-feb'")
    form = Form.from_store(conn)
    conn.close()
    assert [f.date for f in form.before(A, "2026-12-31")] == ["2026-01-10"]
    assert [f.date for f in form.before(B, "2026-12-31")] == ["2026-01-10"]
