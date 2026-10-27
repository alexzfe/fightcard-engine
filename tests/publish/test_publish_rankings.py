"""`fightcard publish` and data/rankings.json: optional, validated, its P4P images must be published
(docs/CONTRACT.md, /v1/rankings.json)."""

from __future__ import annotations

import copy

import pytest
from publish_helpers import RANKINGS, load, make_data, snapshot_members, tree

from fightcard.cli import main
from fightcard.publish.build import CONTRACT_VERSION, build
from fightcard.store import dumps, write_json


def publish(data_dir, out) -> int:
    return main(["publish", "--data", str(data_dir), "--out", str(out)])


def rankings(**changes) -> dict:
    return copy.deepcopy(RANKINGS) | changes


def with_rankings(data_dir, value=None):
    write_json(data_dir / "rankings.json", RANKINGS if value is None else value)
    return data_dir


def refused(data_dir, out, capsys) -> str:
    assert publish(data_dir, out) == 1
    assert not (out / "current").exists()
    return capsys.readouterr().err


def test_the_contract_version_is_1_1():
    assert CONTRACT_VERSION == "1.1"


def test_rankings_json_is_published_as_in_data_when_present(data_dir, out):
    with_rankings(data_dir)
    assert publish(data_dir, out) == 0
    published = (out / "current" / "rankings.json").read_text(encoding="utf-8")
    assert load(out / "current" / "rankings.json") == RANKINGS
    assert published == dumps(RANKINGS)


def test_rankings_json_is_in_the_snapshot(data_dir, out):
    with_rankings(data_dir)
    publish(data_dir, out)
    files = tree(out / "current")
    members = snapshot_members(files.pop("snapshot.tar.gz"))
    assert members == files and "rankings.json" in members


def test_a_data_dir_without_rankings_json_still_publishes(data_dir, out):
    assert publish(data_dir, out) == 0
    assert "rankings.json" not in tree(out / "current")
    assert "rankings.json" not in snapshot_members(tree(out / "current")["snapshot.tar.gz"])


def test_rankings_json_changes_the_generation(tmp_path):
    a = build(make_data(tmp_path / "a"))
    b = build(with_rankings(make_data(tmp_path / "b")))
    assert a["snapshot.tar.gz"] != b["snapshot.tar.gz"]


def test_schema_describes_rankings(data_dir, out):
    publish(data_dir, out)
    schema = load(out / "current" / "schema.json")
    assert {"Rankings", "P4PEntry", "ActiveEntry"} <= set(schema["$defs"])
    assert set(schema["$defs"]["Rankings"]["properties"]) == {"as_of", "p4p", "active"}
    assert "rankings.json" in schema["description"]


def test_a_p4p_fighter_need_not_be_in_fighters_json(data_dir, out):
    """g9 and h7 are on no card: rankings cover every rated fighter."""
    with_rankings(data_dir)
    assert publish(data_dir, out) == 0


def test_a_null_image_is_fine(data_dir, out):
    value = rankings()
    for e in value["p4p"]["men"]:
        e["image"] = None
    with_rankings(data_dir, value)
    assert publish(data_dir, out) == 0


def test_a_p4p_image_missing_from_the_published_images_is_refused(data_dir, out, capsys):
    value = rankings()
    value["p4p"]["men"][1]["image"] = "images/g9.webp"
    message = refused(with_rankings(data_dir, value), out, capsys)
    assert "rankings.json" in message and "images/g9.webp" in message and "g9" in message


def test_a_p4p_image_whose_file_is_not_a_published_type_is_refused(data_dir, out, capsys):
    (data_dir / "images" / "g9.txt").write_text("not an image")
    value = rankings()
    value["p4p"]["men"][1]["image"] = "images/g9.txt"
    assert "images/g9.txt" in refused(with_rankings(data_dir, value), out, capsys)


def test_a_p4p_image_when_there_is_no_images_dir_is_refused(tmp_path, out, capsys):
    data = make_data(tmp_path / "data")
    for p in (data / "images").iterdir():
        p.unlink()
    (data / "images").rmdir()
    assert "images/f1.webp" in refused(with_rankings(data), out, capsys)


def break_(path: str, value):
    def edit(r: dict) -> dict:
        node = r
        *keys, last = path.split(".")
        for k in keys:
            node = node[int(k)] if k.isdigit() else node[k]
        if value is KeyError:
            del node[int(last) if last.isdigit() else last]
        else:
            node[int(last) if last.isdigit() else last] = value
        return r
    return edit


INVALID = {
    "no as_of": break_("as_of", KeyError),
    "as_of not a date": break_("as_of", "yesterday"),
    "no p4p": break_("p4p", KeyError),
    "no women list": break_("p4p.women", KeyError),
    "no active": break_("active", KeyError),
    "elo a string": break_("p4p.men.0.elo", "high"),
    "rank 0": break_("p4p.men.0.rank", 0),
    "ranks not 1..n": break_("p4p.men.1.rank", 3),
    "ranks out of order": lambda r: r["p4p"]["men"].reverse() or r,
    "16 men": lambda r: r["p4p"]["men"].extend(
        {**r["p4p"]["men"][0], "rank": i, "id": f"m{i}", "image": None} for i in range(2, 17)) or r,
    "11 women": lambda r: r["p4p"]["women"].extend(
        {**r["p4p"]["women"][0], "rank": i, "id": f"w{i}", "image": None} for i in range(2, 12)) or r,
    "p4p entry without image key": lambda r: r["p4p"]["men"][0].pop("image") and r,
    "active row without provisional": break_("active.0.provisional", KeyError),
    "active p4p_rank a string": break_("active.0.p4p_rank", "1"),
}


@pytest.mark.parametrize("edit", INVALID.values(), ids=INVALID)
def test_an_invalid_rankings_json_is_refused(data_dir, out, capsys, edit):
    assert "rankings.json" in refused(with_rankings(data_dir, edit(rankings())), out, capsys)


@pytest.mark.parametrize("text", ["{not json", "[]", "null"], ids=["unparseable", "a list", "null"])
def test_a_rankings_json_that_is_not_an_object_is_refused(data_dir, out, capsys, text):
    (data_dir / "rankings.json").write_text(text, encoding="utf-8")
    assert "rankings.json" in refused(data_dir, out, capsys)
