"""fightcard-service health, the container's HEALTHCHECK: nginx answers, what it serves is fresh, no run is stuck.

nginx's own /healthz (what infra's public smoke test hits) stays a plain 200; this is the container's check."""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fightcard.service import health, runner
from fightcard.service.runner import Config

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def config(tmp_path: Path, fetch: bool = True) -> Config:
    return Config(root=tmp_path / "srv", fetch=fetch, fightcard="fightcard", model_dir=Path("model"),
                  gate_script=REPO / "scripts" / "should_run.py", python=sys.executable)


@pytest.fixture(scope="module")
def nginx():
    """A local server answering /healthz with 200, standing in for nginx."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/healthz" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/healthz"
    server.shutdown()


def publish(cfg: Config, generated_at: datetime | str) -> None:
    gen = cfg.root / "generations" / "g1"
    gen.mkdir(parents=True)
    text = generated_at if isinstance(generated_at, str) else generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    (gen / "index.json").write_text(json.dumps({"generated_at": text, "upcoming": [], "recent": []}))
    (cfg.root / "current").symlink_to("generations/g1")


def busy(cfg: Config, started: datetime, pid: int | None = None) -> None:
    cfg.state.mkdir(parents=True, exist_ok=True)
    cfg.run_marker.write_text(json.dumps({"pid": pid or os.getpid(), "started": started.isoformat()}))


def problems(cfg: Config, url: str) -> list[str]:
    return health.check(cfg, now=NOW, url=url)


def test_an_unseeded_volume_is_healthy(tmp_path, nginx):
    # The first deploy comes before seeding, and must not be rolled back.
    assert problems(config(tmp_path), nginx) == []


def test_nginx_not_answering_is_unhealthy(tmp_path):
    found = problems(config(tmp_path), "http://127.0.0.1:9/healthz")
    assert found and "nginx" in found[0]


@pytest.mark.parametrize("age,healthy", [(timedelta(hours=25, minutes=59), True), (timedelta(hours=26, minutes=1),
                                                                                       False)])
def test_what_is_served_must_be_under_26_hours_old(tmp_path, nginx, age, healthy):
    cfg = config(tmp_path)
    publish(cfg, NOW - age)
    found = problems(cfg, nginx)
    assert (found == []) is healthy, found
    if not healthy:
        assert "generated_at" in found[0]


def test_staging_never_scrapes_so_its_data_age_is_not_checked(tmp_path, nginx):
    cfg = config(tmp_path, fetch=False)
    publish(cfg, NOW - timedelta(days=30))
    assert problems(cfg, nginx) == []


def test_an_unreadable_index_is_unhealthy(tmp_path, nginx):
    cfg = config(tmp_path)
    publish(cfg, "not a date")
    assert problems(cfg, nginx)


@pytest.mark.parametrize("age,healthy", [(timedelta(hours=1, minutes=59), True), (timedelta(hours=2, minutes=1),
                                                                                      False)])
def test_a_run_busy_for_over_2_hours_is_unhealthy(tmp_path, nginx, age, healthy):
    cfg = config(tmp_path)
    busy(cfg, NOW - age)
    found = problems(cfg, nginx)
    assert (found == []) is healthy, found
    if not healthy:
        assert "busy" in found[0]


def test_the_marker_of_a_dead_run_does_not_count(tmp_path, nginx):
    cfg = config(tmp_path)
    busy(cfg, NOW - timedelta(hours=5), pid=2**22 + 1)  # above pid_max: never alive
    assert problems(cfg, nginx) == []


def test_the_busy_limit_is_above_the_run_cap():
    from fightcard.service import serve
    assert serve.RUN_TIMEOUT < health.BUSY_LIMIT.total_seconds() == 2 * 3600
    assert health.STALE_AFTER == timedelta(hours=26)


def test_a_run_marks_itself_busy_while_its_stages_run(tmp_path):
    cfg = config(tmp_path)
    seen = []

    def execute(argv, **_):
        seen.append(json.loads(cfg.run_marker.read_text())["pid"])
        return 0

    runner.run_once(cfg, execute=execute, out=lambda line: None, run_id="r1")
    assert seen and set(seen) == {os.getpid()}
    assert not cfg.run_marker.exists()


def test_cli_health_exits_0_or_1(tmp_path, nginx, monkeypatch, capsys):
    from fightcard.service import __main__ as cli
    monkeypatch.setattr(health, "URL", nginx)
    monkeypatch.setenv("FIGHTCARD_ROOT", str(tmp_path / "srv"))
    monkeypatch.setenv("FIGHTCARD_FETCH", "on")
    assert cli.main(["health"]) == 0
    publish(config(tmp_path), "2020-01-01T00:00:00Z")
    assert cli.main(["health"]) == 1
    assert "generated_at" in capsys.readouterr().out
