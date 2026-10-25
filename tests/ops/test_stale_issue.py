import pytest

from conftest import write_json
from stale_issue import MARKER, TITLE, build_body, load_stale, main


def stale(fighter_id, name, event_id):
    return {"fighter_id": fighter_id, "name": name, "event_id": event_id}


def test_load_stale_missing_file_is_empty(data_dir):
    assert load_stale(data_dir) == []


def test_load_stale_reads_list(data_dir):
    write_json(data_dir / "stale_fighters.json", [stale("f1", "Ann", "e1")])
    assert load_stale(data_dir) == [stale("f1", "Ann", "e1")]


def test_load_stale_rejects_entry_without_fighter_id(data_dir):
    write_json(data_dir / "stale_fighters.json", [{"name": "Ann", "event_id": "e1"}])
    with pytest.raises(ValueError):
        load_stale(data_dir)


def test_load_stale_rejects_non_list(data_dir):
    write_json(data_dir / "stale_fighters.json", {"f1": "Ann"})
    with pytest.raises(ValueError):
        load_stale(data_dir)


def test_body_has_marker_count_and_model_date():
    body = build_body([stale("f1", "Ann", "e1")], events={}, model_as_of="2026-10-01")
    assert body.startswith(MARKER)
    assert "1 fighter" in body
    assert "2026-10-01" in body


def test_body_links_fighters_and_names_events():
    body = build_body(
        [stale("abc123", "Ann Smith", "e1")],
        events={"e1": "UFC 999: Smith vs Doe"},
        model_as_of=None,
    )
    assert "[Ann Smith](http://ufcstats.com/fighter-details/abc123)" in body
    assert "UFC 999: Smith vs Doe" in body


def test_body_falls_back_to_event_id_when_name_unknown():
    body = build_body([stale("f1", "Ann", "e-unknown")], events={}, model_as_of=None)
    assert "e-unknown" in body


def test_body_dedupes_and_sorts_by_name():
    body = build_body(
        [stale("f2", "Zed", "e1"), stale("f1", "Ann", "e1"), stale("f2", "Zed", "e1")],
        events={},
        model_as_of=None,
    )
    assert "2 fighters" in body
    assert body.count("Zed") == 1
    assert body.index("Ann") < body.index("Zed")


def test_body_tells_how_to_re_export():
    body = build_body([stale("f1", "Ann", "e1")], events={}, model_as_of=None)
    assert "uv run python -m export" in body
    assert "model/" in body


def test_body_escapes_pipes_in_names():
    body = build_body([stale("f1", "A|B", "e1")], events={"e1": "X|Y"}, model_as_of=None)
    assert "A\\|B" in body
    assert "X\\|Y" in body


def test_title_is_stable():
    assert TITLE == "Re-export finish model"


def test_main_with_stale_writes_body_and_outputs(data_dir, make_event, make_index, tmp_path, monkeypatch):
    make_event("e1", "2026-10-25", name="UFC 999")
    make_index(upcoming=["e1"], model={"as_of": "2026-10-01", "version": "1"})
    write_json(data_dir / "stale_fighters.json", [stale("f1", "Ann", "e1")])
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    body_path = tmp_path / "body.md"

    assert main(["--data", str(data_dir), "--out", str(body_path)]) == 0

    body = body_path.read_text()
    assert "UFC 999" in body and "2026-10-01" in body
    assert out.read_text() == "stale=true\ncount=1\n"


def test_main_without_stale_writes_no_body(data_dir, tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    body_path = tmp_path / "body.md"

    assert main(["--data", str(data_dir), "--out", str(body_path)]) == 0

    assert not body_path.exists()
    assert out.read_text() == "stale=false\ncount=0\n"


def test_main_tolerates_missing_index_and_events(data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    write_json(data_dir / "stale_fighters.json", [stale("f1", "Ann", "e1")])
    body_path = tmp_path / "body.md"
    assert main(["--data", str(data_dir), "--out", str(body_path)]) == 0
    assert "stale=true" in capsys.readouterr().out
    assert "e1" in body_path.read_text()
