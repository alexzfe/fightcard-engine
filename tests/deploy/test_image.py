"""The service image (Dockerfile, docs/SERVICE.md): nginx and the scheduler in one non-root container.

The static checks run by default. `uv run pytest -m docker` builds the image and runs it the way infra does: an
empty named volume at /srv/fightcard, a memory cap, fetching off. It seeds the volume as docs/SERVICE.md says, then
checks /healthz, the published /v1/, the user the processes run as, the cgroup's peak memory, and that a failed
run leaves the last good generation live."""

from __future__ import annotations

import http.client
import io
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "publish"))
from publish_helpers import make_data  # noqa: E402

DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"
MEM_CAP = 768 * 2**20  # staging_mem_limit in infra's sites.yml, the smaller of the two caps
GENERATED_AT = "2026-10-25T23:20:24Z"


def dockerfile() -> str:
    assert DOCKERFILE.is_file(), "Dockerfile is missing"
    return DOCKERFILE.read_text()


# Static checks.

def test_every_base_image_is_pinned_by_tag_and_digest():
    text = dockerfile()
    images = re.findall(r"^(?:ARG \w+=|FROM )(\S+)", text, re.M)
    images = [i for i in images if not i.startswith("$")]
    assert images
    for image in images:
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_runtime_is_nginx_unprivileged_and_multi_stage():
    text = dockerfile()
    assert len(re.findall(r"^FROM ", text, re.M)) >= 2
    assert "nginxinc/nginx-unprivileged:1.28-" in text
    assert "COPY --from=build" in text


def test_dependencies_are_baked_in_from_the_lock():
    text = dockerfile()
    assert "uv sync --frozen --no-dev" in text
    assert "--no-editable" in text


def test_runs_as_a_non_root_user():
    users = re.findall(r"^USER (\S+)", dockerfile(), re.M)
    assert users and users[-1] not in ("root", "0")


def test_serves_the_repo_nginx_config_on_8080_with_a_healthz_check():
    text = dockerfile().replace("\\\n", " ")  # join continuation lines
    assert "COPY deploy/nginx/fightcard-engine.conf /etc/nginx/conf.d/default.conf" in text
    assert re.search(r"^EXPOSE 8080$", text, re.M)
    # The container's check is the supervisor's (nginx up, data fresh, no stuck run); nginx's /healthz stays a
    # plain 200 for infra's public smoke test.
    assert re.search(r'^HEALTHCHECK .*CMD \["fightcard-service", "health"\]$', text, re.M)


def test_pid_1_is_the_supervisor_and_stops_on_sigterm():
    text = dockerfile()
    assert 'ENTRYPOINT ["fightcard-service"]' in text
    assert 'CMD ["serve"]' in text
    assert re.search(r"^STOPSIGNAL SIGTERM$", text, re.M)  # the nginx base image says SIGQUIT


def test_the_publish_root_is_the_nginx_root():
    from fightcard.service.runner import ROOT
    assert f"FIGHTCARD_ROOT={ROOT}" in dockerfile()
    assert f"alias {ROOT}/current/" in (REPO / "deploy" / "nginx" / "fightcard-engine.conf").read_text()


def test_dockerignore_admits_only_what_the_image_needs():
    lines = [line for line in DOCKERIGNORE.read_text().splitlines() if line and not line.startswith("#")]
    assert lines[0] == "*"
    allowed = {line[1:] for line in lines if line.startswith("!")}
    assert {"pyproject.toml", "uv.lock", "src", "model", "deploy/nginx", "scripts/should_run.py",
            "scripts/gh_output.py"} <= allowed
    for path in allowed:
        assert (REPO / path).exists(), path


# Against the built image.

def docker(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kwargs)


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def wait_for(predicate, what: str, timeout: float = 60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := predicate():
                return result
        except OSError:
            pass
        time.sleep(0.5)
    pytest.fail(f"timed out waiting for {what}")


def history_store(db: Path) -> None:
    """A store holding a bout for the fixture's completed event e2, so score's staleness check passes."""
    from fightcard.history.store import connect, upsert_event
    from fightcard.models import Event, Fight, Result
    conn = connect(db)
    fight = Fight(id="e2-1", red="f1", blue="f2", weight_class="Lightweight",
                  result=Result(winner="f1", outcome="win", method="U-DEC", round=3, time="5:00"))
    with conn:
        upsert_event(conn, Event(id="e2", slug="ufc-0", name="UFC 0", date="2026-10-18", status="completed",
                                 fights=[fight]), is_ufc=True, source="listing")
    conn.close()


def seed_tarball(tmp: Path) -> bytes:
    """data/ and history/history.sqlite, as docs/SERVICE.md's seeding step streams them in."""
    make_data(tmp / "data", generated_at=GENERATED_AT)
    history_store(tmp / "history" / "history.sqlite")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tmp / "data", arcname="data")
        tar.add(tmp / "history", arcname="history")
    return buf.getvalue()


class Container:
    def __init__(self, name: str, port: int):
        self.name, self.port = name, port

    def logs(self) -> str:
        result = docker("logs", self.name, check=False)
        return result.stdout + result.stderr

    def exec(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", "exec", "-i", self.name, *args], input=input, capture_output=True)

    def run_lines(self) -> list[str]:
        return [line for line in self.logs().splitlines() if line.startswith("fightcard-run ")]


@pytest.fixture(scope="module")
def image():
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    tag = f"fightcard-engine:test-{uuid.uuid4().hex[:8]}"
    build = docker("build", "-t", tag, str(REPO), check=False)
    assert build.returncode == 0, build.stderr[-4000:]
    size = int(docker("image", "inspect", "-f", "{{.Size}}", tag).stdout)
    print(f"image {tag}: {size / 2**20:.0f} MiB")
    yield tag
    docker("rmi", "-f", tag, check=False)


@pytest.fixture(scope="module")
def container(image, tmp_path_factory):
    name = f"fightcard-engine-test-{uuid.uuid4().hex[:8]}"
    volume = f"{name}-data"
    docker("volume", "create", volume)
    run = docker("run", "-d", "--name", name, "-p", "127.0.0.1::8080", "--memory", str(MEM_CAP),
                 "--memory-swap", str(MEM_CAP), "--health-interval", "1s", "--health-start-period", "1s",
                 "-e", "FIGHTCARD_FETCH=off", "--mount", f"type=volume,src={volume},dst=/srv/fightcard",
                 image, check=False)
    try:
        assert run.returncode == 0, run.stderr
        port = int(docker("port", name, "8080").stdout.splitlines()[0].rsplit(":", 1)[1])
        c = Container(name, port)
        c.tarball = seed_tarball(tmp_path_factory.mktemp("seed"))
        yield c
    finally:
        print(Container(name, 0).logs()[-6000:])
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", "-f", volume, check=False)


def ends(lines: list[str]) -> list[str]:
    return [line for line in lines if " status=" in line and " step=" not in line and "status=start" not in line]


@pytest.mark.docker
def test_healthz_answers_and_docker_reports_healthy(container):
    wait_for(lambda: get(container.port, "/healthz")[0] == 200, "/healthz")
    assert get(container.port, "/healthz") == (200, b"ok\n")
    wait_for(lambda: docker("inspect", "-f", "{{.State.Health.Status}}", container.name).stdout.strip()
             == "healthy", "a healthy HEALTHCHECK")


@pytest.mark.docker
def test_the_startup_run_on_an_empty_volume_fails_without_publishing(container):
    wait_for(lambda: ends(container.run_lines()), "the startup run to end")
    lines = container.run_lines()
    print("\n".join(lines))
    assert "event=startup" in lines[0] and "fetch=off" in lines[0]
    assert "step=scrape status=skipped reason=fetch-off" in "\n".join(lines)
    assert "status=failed" in ends(lines)[0]
    assert get(container.port, "/v1/index.json")[0] == 404


@pytest.mark.docker
def test_seeding_then_a_manual_run_publishes_the_api(container):
    seed = container.exec("sh", "-c", "mkdir -p /srv/fightcard/state && tar -xzf - -C /srv/fightcard/state",
                          input=container.tarball)
    assert seed.returncode == 0, seed.stderr
    run = container.exec("fightcard-service", "run")
    out = run.stdout.decode()
    print(out, run.stderr.decode()[-2000:])
    assert run.returncode == 0
    assert "step=history" not in out  # scrape upserts the store; there is no separate step
    assert "score: history store /srv/fightcard/state/history/history.sqlite" in run.stderr.decode() + out
    for step in ("score", "publish"):
        assert re.search(rf"step={step} status=ok exit=0 duration_s=\d+\.\d\d", out), step
    status, body = get(container.port, "/v1/index.json")
    assert status == 200 and GENERATED_AT.encode() in body
    assert get(container.port, "/v1/events/e1.json")[0] == 200
    assert get(container.port, "/v1/snapshot.tar.gz")[0] == 200


# infra's sites.yml `backup:` for ufc-data; backup.j2 runs it as `docker compose exec -T app <command>`.
BACKUP = ["fightcard", "history", "backup", "--db", "/srv/fightcard/state/history/history.sqlite"]


@pytest.mark.docker
def test_the_history_backup_streams_a_tgz_the_way_infra_runs_it(container):
    out = subprocess.run(["docker", "exec", container.name, *BACKUP], capture_output=True)
    assert out.returncode == 0, out.stderr
    with tarfile.open(fileobj=io.BytesIO(out.stdout), mode="r:gz") as tar:
        assert tar.getnames() == ["history/history.sqlite"]


@pytest.mark.docker
def test_a_failed_run_leaves_the_last_good_generation_live(container):
    before = get(container.port, "/v1/index.json")
    broken = container.exec("sh", "-c", "echo '{not json' > /srv/fightcard/state/data/index.json")
    assert broken.returncode == 0
    run = container.exec("fightcard-service", "run")
    print(run.stdout.decode())
    assert run.returncode != 0
    assert "status=failed" in run.stdout.decode().splitlines()[-1]
    assert get(container.port, "/v1/index.json") == before


@pytest.mark.docker
def test_the_healthcheck_stays_healthy_after_publishing(container):
    assert container.exec("fightcard-service", "health").returncode == 0
    wait_for(lambda: docker("inspect", "-f", "{{.State.Health.Status}}", container.name).stdout.strip()
             == "healthy", "a healthy HEALTHCHECK after publishing")


@pytest.mark.docker
def test_the_healthcheck_fails_where_fetching_is_on_and_the_data_is_stale(container):
    index = "/srv/fightcard/current/index.json"
    original = container.exec("cat", index).stdout
    stale = container.exec("sh", "-c", f"sed -i 's/\"generated_at\": *\"[^\"]*\"/\"generated_at\": "
                                       f"\"2020-01-01T00:00:00Z\"/' {index}")
    assert stale.returncode == 0, stale.stderr
    try:
        check = subprocess.run(["docker", "exec", "-e", "FIGHTCARD_FETCH=on", container.name, "fightcard-service",
                                "health"], capture_output=True, text=True)
        print(check.stdout, check.stderr)
        assert check.returncode == 1 and "generated_at" in check.stdout
        # This container fetches nothing (FIGHTCARD_FETCH=off, like staging), so its own check doesn't count age.
        assert container.exec("fightcard-service", "health").returncode == 0
    finally:
        container.exec("sh", "-c", f"cat > {index}", input=original)


@pytest.mark.docker
def test_every_process_runs_as_non_root(container):
    assert container.exec("id", "-u").stdout.decode().strip() == "101"
    top = docker("top", container.name, "-eo", "uid,pid,args").stdout.splitlines()[1:]  # docker top needs pid
    print("\n".join(top))
    assert top and all(line.split()[0] != "0" for line in top), top
    assert any("nginx" in line for line in top) and any("fightcard-service serve" in line for line in top)


@pytest.mark.docker
def test_peak_memory_stays_under_the_cap(container):
    peak = int(container.exec("cat", "/sys/fs/cgroup/memory.peak").stdout)
    print(f"memory.peak {peak / 2**20:.0f} MiB of {MEM_CAP / 2**20:.0f} MiB")
    assert docker("inspect", "-f", "{{.State.OOMKilled}}", container.name).stdout.strip() == "false"
    assert peak < MEM_CAP


@pytest.mark.docker
def test_sigterm_stops_the_container_promptly(container):
    t0 = time.monotonic()
    docker("stop", "-t", "10", container.name)
    elapsed = time.monotonic() - t0
    code = int(docker("inspect", "-f", "{{.State.ExitCode}}", container.name).stdout)
    print(f"stopped in {elapsed:.1f}s, exit {code}")
    assert code == 0 and elapsed < 8
