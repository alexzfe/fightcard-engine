import json

import pytest
from fightcard.models import Event, Fight, Fighter, Index, ModelInfo
from fightcard.store import Store, read_json, write_json


def event(id="e1", **kw):
    data = dict(id=id, slug="ufc-1", name="UFC 1", date="2026-11-01", status="upcoming",
                fights=[Fight(id="f1", red="a", blue="b")])
    data.update(kw)
    return Event(**data)


def test_write_json_is_sorted_two_space_indented_with_trailing_newline(tmp_path):
    path = tmp_path / "sub" / "x.json"
    write_json(path, {"b": 1, "a": {"d": "Kareckaitė", "c": [1, 2]}})
    assert path.read_text(encoding="utf-8") == (
        '{\n  "a": {\n    "c": [\n      1,\n      2\n    ],\n    "d": "Kareckaitė"\n  },\n  "b": 1\n}\n'
    )


def test_write_json_leaves_no_temp_files(tmp_path):
    write_json(tmp_path / "x.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_read_json_default_when_missing(tmp_path):
    assert read_json(tmp_path / "nope.json", default={}) == {}


def test_event_round_trip(tmp_path):
    store = Store(tmp_path)
    store.save_event(event())
    assert (tmp_path / "events" / "e1.json").exists()
    assert store.load_events() == {"e1": event()}
    assert store.load_event("e1") == event()
    assert store.load_event("missing") is None


def test_event_file_keeps_null_fields_for_a_stable_schema(tmp_path):
    Store(tmp_path).save_event(event())
    data = json.loads((tmp_path / "events" / "e1.json").read_text())
    assert data["fights"][0]["finish_tier"] is None
    assert data["location"] is None


def test_load_events_on_empty_dir(tmp_path):
    assert Store(tmp_path).load_events() == {}


def test_fighters_round_trip(tmp_path):
    store = Store(tmp_path)
    fighters = {"b": Fighter(id="b", name="B"), "a": Fighter(id="a", name="A", image="images/a.png")}
    store.save_fighters(fighters)
    assert store.load_fighters() == fighters
    assert list(json.loads((tmp_path / "fighters.json").read_text())) == ["a", "b"]


def test_fighters_missing_file_is_empty(tmp_path):
    assert Store(tmp_path).load_fighters() == {}


def test_index_round_trip(tmp_path):
    store = Store(tmp_path)
    assert store.load_index() is None
    index = Index(generated_at="2026-10-24T00:00:00Z", upcoming=["e1"], recent=[],
                  model=ModelInfo(as_of="2026-10-01", version="1"))
    store.save_index(index)
    assert store.load_index() == index


def test_failed_write_keeps_old_file_and_cleans_up(tmp_path):
    path = tmp_path / "x.json"
    write_json(path, {"a": 1})
    with pytest.raises(TypeError):
        write_json(path, {"a": object()})
    assert json.loads(path.read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
