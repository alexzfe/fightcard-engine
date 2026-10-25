from datetime import date

import pytest

import should_run
from should_run import DAILY_CRON, decide, main

HOURLY = "17 * * * *"
TODAY = date(2026, 10, 25)


def test_manual_dispatch_always_runs(data_dir, make_index):
    make_index()
    run, _ = decide(data_dir, event_name="workflow_dispatch", schedule="", today=TODAY)
    assert run


def test_daily_schedule_always_runs(data_dir, make_index):
    make_index()
    run, reason = decide(data_dir, event_name="schedule", schedule=DAILY_CRON, today=TODAY)
    assert run
    assert "daily" in reason


def test_hourly_skips_when_no_event_near(data_dir, make_index, make_event):
    make_event("a", "2026-11-01")
    make_event("b", "2026-10-18", status="completed")
    make_index(upcoming=["a"], recent=["b"])
    run, reason = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert not run
    assert "no event" in reason


def test_hourly_runs_on_event_day(data_dir, make_index, make_event):
    make_event("a", "2026-10-25")
    make_index(upcoming=["a"])
    run, reason = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert run
    assert "a" in reason


def test_hourly_runs_day_after_event_for_results(data_dir, make_index, make_event):
    # US cards end after midnight UTC, and results are scraped the day after.
    make_event("b", "2026-10-24", status="completed")
    make_index(recent=["b"])
    run, _ = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert run


def test_hourly_skips_two_days_after_event(data_dir, make_index, make_event):
    make_event("b", "2026-10-23", status="completed")
    make_index(recent=["b"])
    run, _ = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert not run


def test_hourly_skips_day_before_event(data_dir, make_index, make_event):
    make_event("a", "2026-10-26")
    make_index(upcoming=["a"])
    run, _ = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert not run


def test_missing_event_file_is_ignored(data_dir, make_index, make_event):
    make_event("a", "2026-10-25")
    make_index(upcoming=["ghost", "a"])
    run, _ = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert run


def test_missing_index_runs_to_bootstrap(data_dir):
    run, reason = decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)
    assert run
    assert "index" in reason


def test_corrupt_index_raises(data_dir):
    (data_dir / "index.json").write_text("{not json")
    with pytest.raises(ValueError):
        decide(data_dir, event_name="schedule", schedule=HOURLY, today=TODAY)


def test_main_writes_github_output(data_dir, make_index, make_event, tmp_path, monkeypatch, capsys):
    make_event("a", "2026-10-25")
    make_index(upcoming=["a"])
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    rc = main(
        ["--data", str(data_dir), "--event-name", "schedule", "--schedule", HOURLY, "--today", "2026-10-25"]
    )
    assert rc == 0
    assert out.read_text() == "run=true\n"
    assert "run=true" in capsys.readouterr().out


def test_main_skip_without_github_output(data_dir, make_index, monkeypatch, capsys):
    make_index()
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    rc = main(
        ["--data", str(data_dir), "--event-name", "schedule", "--schedule", HOURLY, "--today", "2026-10-25"]
    )
    assert rc == 0
    assert "run=false" in capsys.readouterr().out


def test_main_defaults_today_to_utc(data_dir, make_index, make_event, monkeypatch, capsys):
    monkeypatch.setattr(should_run, "utc_today", lambda: TODAY)
    make_event("a", "2026-10-25")
    make_index(upcoming=["a"])
    main(["--data", str(data_dir), "--event-name", "schedule", "--schedule", HOURLY])
    assert "run=true" in capsys.readouterr().out
