from datetime import datetime, timezone

from fightcard.images.misses import Misses

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_missing_file_means_no_misses(tmp_path):
    m = Misses.load(tmp_path / "misses.json")
    assert not m.recently_tried("abc", NOW)


def test_recent_miss_is_skipped_old_miss_is_retried(tmp_path):
    path = tmp_path / "misses.json"
    path.write_text('{"recent": "2026-10-20T00:00:00Z", "old": "2026-10-10T00:00:00Z"}\n')
    m = Misses.load(path)
    assert m.recently_tried("recent", NOW)
    assert not m.recently_tried("old", NOW)


def test_exactly_seven_days_is_retried(tmp_path):
    path = tmp_path / "misses.json"
    path.write_text('{"a": "2026-10-17T12:00:00Z"}\n')
    assert not Misses.load(path).recently_tried("a", NOW)


def test_record_and_clear_then_save_formats_per_contract(tmp_path):
    path = tmp_path / "images" / "misses.json"
    m = Misses.load(path)
    m.record("zz", NOW)
    m.record("aa", NOW)
    m.record("gone", NOW)
    m.clear("gone")
    m.save()
    assert path.read_text() == (
        '{\n  "aa": "2026-10-24T12:00:00Z",\n  "zz": "2026-10-24T12:00:00Z"\n}\n'
    )


def test_corrupt_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "misses.json"
    path.write_text("{nope")
    m = Misses.load(path)
    assert not m.recently_tried("a", NOW)


def test_bad_timestamp_is_retried(tmp_path):
    path = tmp_path / "misses.json"
    path.write_text('{"a": "yesterday"}')
    assert not Misses.load(path).recently_tried("a", NOW)
