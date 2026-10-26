"""The store seeded from research's full ufcstats cache agrees with research's parsed CSVs (`-m seed`)."""

from __future__ import annotations

import json

import pytest
from history_helpers import SEED_CSV, cross_check, read_csv

pytestmark = pytest.mark.seed

# Completed bouts on cached event pages that research's fight_data.csv lacks (ufcscraper builds fight rows
# from fight-details pages, and these aren't in the cache). All on old PRIDE / Strikeforce cards.
NOT_IN_RESEARCH = {"502e759919cd1daa", "a47eaaf3f292f06c", "4711c195cc941c78", "0dd8f71a1a628550",
                   "7542088bdbe767dc", "db72f0b4d2a34a6a", "59ef971b74b6abf5", "c2df05f52e20b892",
                   "c5d5dcd8a5f2ee7b", "7f0e5ac5bcb5a0f3", "869f7107a45b499c", "c53327add2bbda93",
                   "b2c8167a19e908b8", "561f0b0c0cee17e2"}
RESEARCH_AS_OF = "2026-10-14"  # research's CSVs were replayed then


def test_every_research_bout_matches_on_id_event_fighters_winner_and_method(seed_db):
    rows = read_csv(SEED_CSV / "fight_data.csv")
    assert len(rows) == 11_600
    assert cross_check(seed_db, rows) == {}


def test_the_store_has_no_completed_bout_up_to_research_as_of_beyond_the_known_gaps(seed_db):
    research = {r["fight_id"] for r in read_csv(SEED_CSV / "fight_data.csv")}
    ours = {r[0] for r in seed_db.execute(
        "SELECT fight_id FROM fights JOIN events USING (event_id) WHERE outcome IS NOT NULL AND date <= ?",
        (RESEARCH_AS_OF,))}
    assert ours - research == NOT_IN_RESEARCH


def test_ufc_rule_agrees_with_research_is_ufc_on_every_event(seed_db):
    source = read_csv(SEED_CSV / "event_source.csv")
    assert len(source) == 1_280
    ours = {r[0]: r[1:] for r in seed_db.execute("SELECT event_id, is_ufc, source FROM events")}
    assert [r["event_id"] for r in source if (int(r["is_ufc"]), r["source"]) != ours[r["event_id"]]] == []


def test_fighter_dobs_agree_with_research_fighter_data(seed_db):
    research = {r["fighter_id"]: r["fighter_dob"] or None for r in read_csv(SEED_CSV / "fighter_data.csv")}
    ours = dict(seed_db.execute("SELECT fighter_id, dob FROM fighters WHERE fighter_id IN "
                                "(SELECT value FROM json_each(?))", (json.dumps(list(research)),)))
    assert sum(d is not None for d in research.values()) > 4_000
    assert [fid for fid, dob in research.items() if ours.get(fid) != dob] == []
