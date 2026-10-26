"""Atomic publish: each run writes <root>/generations/<id>/ in full, then swaps the <root>/current symlink with
one rename. Readers see the old generation or the new one, never a mix or a gap."""

from __future__ import annotations

import errno
import json
import os
import pathlib
import shutil
import stat
import threading

import pytest
from publish_helpers import load, make_data, tree

from fightcard.cli import main
from fightcard.publish.build import build
from fightcard.publish.generations import publish
from fightcard.store import Store


def variant(tmp_path, n: int) -> dict[str, bytes]:
    """A generation whose every JSON file carries the marker n (snapshot included)."""
    data = make_data(tmp_path / f"data{n}", generated_at=f"2026-10-25T23:{n // 60:02d}:{n % 60:02d}Z")
    store = Store(data)
    fighters = store.load_fighters()
    fighters["f1"] = fighters["f1"].model_copy(update={"name": f"Ana Red {n}"})
    store.save_fighters(fighters)
    for eid, event in store.load_events().items():
        store.save_event(event.model_copy(update={"name": f"{event.name} #{n}"}))
    return build(data)


def generations(root) -> list[str]:
    return sorted(p.name for p in (root / "generations").iterdir())


def live(root) -> str:
    return os.readlink(root / "current")


def test_current_is_a_relative_symlink_to_a_complete_generation(tmp_path):
    files = variant(tmp_path, 1)
    root = tmp_path / "srv"
    gen = publish(files, root)
    assert gen.changed
    assert (root / "current").is_symlink()
    assert live(root) == f"generations/{gen.id}"
    assert generations(root) == [gen.id]
    assert tree(root / "current") == files


def test_a_new_generation_switches_current_and_keeps_the_old_one(tmp_path):
    root = tmp_path / "srv"
    first = publish(variant(tmp_path, 1), root)
    second = publish(variant(tmp_path, 2), root)
    assert first.id != second.id
    assert generations(root) == sorted([first.id, second.id])
    assert live(root) == f"generations/{second.id}"
    assert load(root / "current" / "fighters.json")["f1"]["name"] == "Ana Red 2"


def test_identical_content_publishes_nothing(tmp_path):
    root = tmp_path / "srv"
    first = publish(variant(tmp_path, 1), root)
    before = os.stat(root / "current" / "snapshot.tar.gz")
    again = publish(variant(tmp_path, 1), root)
    assert not again.changed and again.id == first.id
    assert generations(root) == [first.id]
    after = os.stat(root / "current" / "snapshot.tar.gz")
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)  # same ETag


def test_a_dangling_current_is_republished(tmp_path):
    root = tmp_path / "srv"
    first = publish(variant(tmp_path, 1), root)
    shutil.rmtree(root / "generations" / first.id)  # removed by hand
    again = publish(variant(tmp_path, 1), root)
    assert again.changed
    assert tree(root / "current") == variant(tmp_path, 1)


def test_unchanged_files_are_shared_with_the_previous_generation(tmp_path):
    root = tmp_path / "srv"
    first = publish(variant(tmp_path, 1), root)
    second = publish(variant(tmp_path, 2), root)
    old, new = root / "generations" / first.id, root / "generations" / second.id
    for same in ("images/f1.webp", "images/f2.png", "schema.json"):
        a, b = os.stat(old / same), os.stat(new / same)
        assert (a.st_ino, a.st_mtime_ns) == (b.st_ino, b.st_mtime_ns), same
    assert os.stat(old / "index.json").st_ino != os.stat(new / "index.json").st_ino


def test_a_changed_file_gets_a_later_mtime_than_the_one_it_replaces(tmp_path):
    # nginx's ETag is "<mtime in seconds>-<size>": a same-size change written in the same second as (or, after a
    # clock step, before) the live file would keep its ETag and clients would get 304 for stale content.
    root = tmp_path / "srv"
    publish(variant(tmp_path, 1), root)
    live_index = root / "current" / "index.json"
    future = int(os.stat(live_index).st_mtime) + 100
    os.utime(live_index, (future, future))
    size = live_index.stat().st_size
    publish(variant(tmp_path, 2), root)  # index.json: same size, new content
    new = os.stat(root / "current" / "index.json")
    assert new.st_size == size
    assert int(new.st_mtime) > future


def test_keeps_the_last_n_generations(tmp_path):
    root = tmp_path / "srv"
    ids = [publish(variant(tmp_path, n), root, keep=2).id for n in range(4)]
    assert generations(root) == sorted(ids[-2:])
    assert live(root) == f"generations/{ids[-1]}"


def test_generation_ids_sort_in_publish_order(tmp_path):
    root = tmp_path / "srv"
    ids = [publish(variant(tmp_path, n), root).id for n in range(3)]
    assert ids == sorted(ids)


def test_published_tree_is_world_readable_whatever_the_umask(tmp_path):
    old = os.umask(0o077)
    try:
        publish(variant(tmp_path, 1), tmp_path / "srv")
    finally:
        os.umask(old)
    gen = (tmp_path / "srv" / "current").resolve()
    for p in [gen, *gen.rglob("*")]:
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == (0o755 if p.is_dir() else 0o644), p  # nginx runs as another user


def failing_writes(monkeypatch, after: int) -> None:
    real = pathlib.Path.write_bytes
    calls = {"n": 0}

    def write_bytes(self, data):
        calls["n"] += 1
        if calls["n"] > after:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", write_bytes)


def test_a_failure_mid_write_leaves_current_untouched(tmp_path, monkeypatch):
    root = tmp_path / "srv"
    first = publish(variant(tmp_path, 1), root)
    files = variant(tmp_path, 2)
    failing_writes(monkeypatch, after=2)
    with pytest.raises(OSError):
        publish(files, root)
    assert live(root) == f"generations/{first.id}"
    assert generations(root) == [first.id]  # no half-written generation left behind
    assert load(root / "current" / "fighters.json")["f1"]["name"] == "Ana Red 1"


def test_a_crashed_runs_leftovers_are_cleared(tmp_path):
    root = tmp_path / "srv"
    publish(variant(tmp_path, 1), root)
    (root / "generations" / ".tmp-crashed" / "events").mkdir(parents=True)
    (root / ".current-crashed").symlink_to("generations/.tmp-crashed")
    second = publish(variant(tmp_path, 2), root)
    assert generations(root) != [] and all(not g.startswith(".") for g in generations(root))
    assert sorted(p.name for p in root.iterdir()) == ["current", "generations"]
    assert live(root) == f"generations/{second.id}"


def test_readers_never_see_a_mixed_or_missing_generation(tmp_path):
    root = tmp_path / "srv"
    variants = [variant(tmp_path, n) for n in range(40)]
    publish(variants[0], root, keep=100)
    done = threading.Event()
    seen, errors = set(), []

    def reader():
        while not done.is_set():
            try:
                index = json.loads((root / "current" / "index.json").read_text())  # always there, always whole
                gen = (root / "current").resolve()
                marker = load(gen / "index.json")["generated_at"]
                n = int(marker[-3:-1]) + 60 * int(marker[-6:-4])
                assert load(gen / "fighters.json")["f1"]["name"] == f"Ana Red {n}"
                assert load(gen / "events" / "e1.json")["name"] == f"UFC 1 #{n}"
                seen.add(index["generated_at"])
            except Exception as e:  # noqa: BLE001 - collected and asserted below
                errors.append(repr(e))
                return

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        for files in variants[1:]:
            publish(files, root, keep=100)
    finally:
        done.set()
        for t in threads:
            t.join()
    assert errors == []
    assert len(seen) > 1


# Through the stage

def test_stage_reports_the_generation(tmp_path, capsys):
    data, root = make_data(tmp_path / "data"), tmp_path / "srv"
    assert main(["publish", "--data", str(data), "--out", str(root)]) == 0
    assert live(root).removeprefix("generations/") in capsys.readouterr().out
    assert main(["publish", "--data", str(data), "--out", str(root)]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_invalid_data_keeps_the_previous_generation_live(tmp_path, capsys):
    data, root = make_data(tmp_path / "data"), tmp_path / "srv"
    main(["publish", "--data", str(data), "--out", str(root)])
    before = live(root)
    (data / "fighters.json").write_text("{", encoding="utf-8")
    assert main(["publish", "--data", str(data), "--out", str(root)]) == 1
    assert live(root) == before and len(generations(root)) == 1


def test_a_write_failure_exits_1(tmp_path, monkeypatch, capsys):
    data, root = make_data(tmp_path / "data"), tmp_path / "srv"
    failing_writes(monkeypatch, after=0)
    assert main(["publish", "--data", str(data), "--out", str(root)]) == 1
    assert "No space left" in capsys.readouterr().err
    assert not (root / "current").exists()


def test_keep_must_be_positive(tmp_path, capsys):
    data, root = make_data(tmp_path / "data"), tmp_path / "srv"
    with pytest.raises(SystemExit):
        main(["publish", "--data", str(data), "--out", str(root), "--keep", "0"])
