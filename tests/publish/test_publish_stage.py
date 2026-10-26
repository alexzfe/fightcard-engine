"""`fightcard publish --data <data> --out <root>`: validates data/, then publishes the /v1/ files
(docs/CONTRACT.md) under <root>/current."""

from __future__ import annotations

import gzip
import io
import json
import tarfile

import pytest
from publish_helpers import GENERATED_AT, PNG, WEBP, load, make_data, snapshot_members, tree

from fightcard.cli import main
from fightcard.publish.build import CONTRACT_VERSION, build, snapshot
from fightcard.models import ModelInfo
from fightcard.store import Store, dumps, write_json

V1_FILES = {"index.json", "fighters.json", "schema.json", "snapshot.tar.gz",
            "events/e1.json", "events/e2.json", "images/f1.webp", "images/f2.png"}


def publish(data_dir, out, *extra: str) -> int:
    return main(["publish", "--data", str(data_dir), "--out", str(out), *extra])


def test_cli_lists_publish(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "publish" in capsys.readouterr().out


def test_publish_writes_every_v1_file(data_dir, out):
    assert publish(data_dir, out) == 0
    assert set(tree(out / "current")) == V1_FILES


def test_global_data_flag_works_too(data_dir, out):
    assert main(["--data", str(data_dir), "publish", "--out", str(out)]) == 0
    assert (out / "current" / "index.json").is_file()


def test_index_adds_contract_version_and_keeps_generated_at(data_dir, out):
    publish(data_dir, out)
    index = load(out / "current" / "index.json")
    assert index["contract_version"] == CONTRACT_VERSION == "1.1"
    assert index["generated_at"] == GENERATED_AT
    source = load(data_dir / "index.json")
    assert {k: v for k, v in index.items() if k != "contract_version"} == source


def test_events_and_fighters_are_published_as_in_data(data_dir, out):
    publish(data_dir, out)
    current = out / "current"
    assert load(current / "fighters.json") == load(data_dir / "fighters.json")
    for eid in ("e1", "e2"):
        assert load(current / "events" / f"{eid}.json") == load(data_dir / "events" / f"{eid}.json")


def test_headshots_are_copied_and_misses_json_is_not(data_dir, out):
    publish(data_dir, out)
    images = out / "current" / "images"
    assert (images / "f1.webp").read_bytes() == WEBP
    assert (images / "f2.png").read_bytes() == PNG
    assert not (images / "misses.json").exists()


def test_publishes_without_an_images_dir(tmp_path, out):
    data = make_data(tmp_path / "data")
    for p in (data / "images").iterdir():
        p.unlink()
    (data / "images").rmdir()
    assert publish(data, out) == 0
    assert not any(name.startswith("images/") for name in tree(out / "current"))


def test_json_follows_the_formatting_rule(data_dir, out):
    publish(data_dir, out)
    for name, raw in tree(out / "current").items():
        if name.endswith(".json"):
            text = raw.decode("utf-8")
            assert text == dumps(json.loads(text)), name


def test_schema_describes_the_top_level_types(data_dir, out):
    publish(data_dir, out)
    schema = load(out / "current" / "schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    defs = schema["$defs"]
    assert {"Index", "Event", "Fighter", "Fighters"} <= set(defs)
    assert "contract_version" in defs["Index"]["required"]
    assert schema["title"] == "fightcard /v1 (contract 1.1)"
    assert defs["Fighters"] == {"type": "object", "additionalProperties": {"$ref": "#/$defs/Fighter"}}
    assert "fights" in defs["Event"]["properties"]


def test_index_model_stale_fighters_is_published_and_in_the_schema(data_dir, out):
    store = Store(data_dir)
    index = store.load_index()
    index.model = ModelInfo(as_of="2026-10-14", version="2026-10-14+5006318a", stale_fighters=3)
    store.save_index(index)
    publish(data_dir, out)
    published = load(out / "current" / "index.json")
    assert published["model"]["stale_fighters"] == 3
    assert published["model"]["version"] == "2026-10-14+5006318a" and published["model"]["as_of"] == "2026-10-14"
    model = load(out / "current" / "schema.json")["$defs"]["ModelInfo"]
    assert "stale_fighters" in model["properties"]
    assert "stale_fighters" not in model["required"]  # additive: data written before it still validates


def test_snapshot_holds_every_other_file(data_dir, out):
    publish(data_dir, out)
    files = tree(out / "current")
    members = snapshot_members(files.pop("snapshot.tar.gz"))
    assert members == files


def test_snapshot_is_deterministic(tmp_path):
    a = build(make_data(tmp_path / "a"))
    b = build(make_data(tmp_path / "b"))  # same content, written later: other mtimes, other paths
    assert a == b
    blob = a["snapshot.tar.gz"]
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
        gz.read()
        assert gz.mtime == 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = tar.getmembers()
    names = [m.name for m in members]
    assert names == sorted(names)
    for m in members:
        assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == (0, 0, 0, "", "")
        assert m.mode == (0o755 if m.isdir() else 0o644)


def test_snapshot_changes_with_content():
    assert snapshot({"index.json": b"{}\n"}) != snapshot({"index.json": b"{ }\n"})


# Refusing to publish: exit 1, nothing published.

def refused(data_dir, out, capsys) -> str:
    assert publish(data_dir, out) == 1
    assert not (out / "current").exists()
    return capsys.readouterr().err


def test_refuses_an_invalid_event(data_dir, out, capsys):
    event = load(data_dir / "events" / "e1.json")
    del event["status"]
    write_json(data_dir / "events" / "e1.json", event)
    assert "e1.json" in refused(data_dir, out, capsys)


def test_refuses_an_invalid_fighter(data_dir, out, capsys):
    fighters = load(data_dir / "fighters.json")
    fighters["f1"]["record"]["w"] = "ten"
    write_json(data_dir / "fighters.json", fighters)
    assert "fighters.json" in refused(data_dir, out, capsys)


def test_refuses_a_fighter_filed_under_another_id(data_dir, out, capsys):
    fighters = load(data_dir / "fighters.json")
    fighters["f3"] = fighters.pop("f1")
    write_json(data_dir / "fighters.json", fighters)
    assert "f3" in refused(data_dir, out, capsys)


def test_refuses_unparseable_json(data_dir, out, capsys):
    (data_dir / "fighters.json").write_text("{not json", encoding="utf-8")
    assert "fighters.json" in refused(data_dir, out, capsys)


def test_refuses_a_missing_index(data_dir, out, capsys):
    (data_dir / "index.json").unlink()
    assert "index.json" in refused(data_dir, out, capsys)


def test_refuses_a_missing_data_dir(tmp_path, out, capsys):
    refused(tmp_path / "nope", out, capsys)


def test_refuses_an_index_naming_a_missing_event(data_dir, out, capsys):
    (data_dir / "events" / "e2.json").unlink()
    assert "e2" in refused(data_dir, out, capsys)


def test_refuses_a_fight_naming_a_fighter_not_in_fighters_json(data_dir, out, capsys):
    fighters = load(data_dir / "fighters.json")
    del fighters["f2"]
    write_json(data_dir / "fighters.json", fighters)
    message = refused(data_dir, out, capsys)
    assert "e1-1" in message and "f2" in message


@pytest.mark.parametrize("generated_at", ["2026-10-25T23:20:24", "2026-10-25T23:20:24+02:00", "yesterday"])
def test_refuses_a_generated_at_that_is_not_utc_z(tmp_path, out, capsys, generated_at):
    data = make_data(tmp_path / "data", generated_at=generated_at)
    assert "generated_at" in refused(data, out, capsys)
