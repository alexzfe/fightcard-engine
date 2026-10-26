"""deploy/nginx/fightcard-engine.conf serving a published tree: the /v1/ API's status codes and headers
(docs/API.md), checked against the real nginx-unprivileged image.

Slow (starts a container): run with `uv run pytest -m docker`. The static checks run by default."""

from __future__ import annotations

import http.client
import io
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

import pytest

from fightcard.publish.build import build
from fightcard.publish.generations import publish

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "publish"))
from publish_helpers import make_data  # noqa: E402

CONF = REPO / "deploy" / "nginx" / "fightcard-engine.conf"
IMAGE = ("nginxinc/nginx-unprivileged:1.28-alpine"
         "@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8")
MOUNT = "/srv/fightcard"
JSON = "application/json; charset=utf-8"


def generation(tmp: Path, generated_at: str) -> dict[str, bytes]:
    return build(make_data(tmp / f"data-{uuid.uuid4().hex[:6]}", generated_at=generated_at))


# Static checks: run in the default suite, so CI (no docker) still guards the config's shape.

def test_config_listens_on_the_unprivileged_port_and_serves_the_published_root():
    conf = CONF.read_text()
    assert "listen 8080;" in conf
    assert f"{MOUNT}/current/" in conf
    assert "autoindex off;" in conf


def test_config_sets_every_cache_policy_and_no_cors():
    conf = CONF.read_text()
    for value in ("public, max-age=60", "public, max-age=300", "public, max-age=86400", "no-store"):
        assert f'"{value}"' in conf, value
    assert "Access-Control" not in conf


# Against nginx.

def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


class Server:
    def __init__(self, root: Path, port: int):
        self.root, self.port = root, port

    def request(self, method: str, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def get(self, path: str, **headers):
        return self.request("GET", path, headers)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    tmp = tmp_path_factory.mktemp("nginx")
    root = tmp / "srv"
    publish(generation(tmp, "2026-10-25T23:20:24Z"), root)
    name = f"fightcard-nginx-test-{uuid.uuid4().hex[:8]}"
    # --mount, not -v: a missing source is an error instead of a root-owned directory created in its place.
    run = docker("run", "-d", "--name", name, "-p", "127.0.0.1::8080",
                 "--mount", f"type=bind,src={root},dst={MOUNT},readonly",
                 "--mount", f"type=bind,src={CONF},dst=/etc/nginx/conf.d/default.conf,readonly", IMAGE, check=False)
    try:
        assert run.returncode == 0, run.stderr
        port = int(docker("port", name, "8080").stdout.splitlines()[0].rsplit(":", 1)[1])
        srv = Server(root, port)
        for _ in range(40):
            try:
                if srv.get("/healthz")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            pytest.fail("nginx never answered /healthz:\n" + docker("logs", name, check=False).stderr)
        yield srv
    finally:
        docker("rm", "-f", name, check=False)


def published(srv: Server, name: str) -> bytes:
    return (srv.root / "current" / name).read_bytes()


RESOURCES = [
    ("/v1/index.json", "index.json", JSON, "public, max-age=60"),
    ("/v1/events/e1.json", "events/e1.json", JSON, "public, max-age=300"),
    ("/v1/fighters.json", "fighters.json", JSON, "public, max-age=300"),
    ("/v1/schema.json", "schema.json", JSON, "public, max-age=300"),
    ("/v1/images/f1.webp", "images/f1.webp", "image/webp", "public, max-age=86400"),
    ("/v1/images/f2.png", "images/f2.png", "image/png", "public, max-age=86400"),
    ("/v1/snapshot.tar.gz", "snapshot.tar.gz", "application/gzip", "public, max-age=60"),
]


@pytest.mark.docker
@pytest.mark.parametrize("path,name,content_type,cache", RESOURCES)
def test_resources_are_served_with_their_type_and_cache_policy(server, path, name, content_type, cache):
    status, headers, body = server.get(path)
    print(path, status, {k: headers.get(k) for k in ("content-type", "cache-control", "etag", "last-modified")})
    assert status == 200
    assert body == published(server, name)
    assert headers["content-type"] == content_type
    assert headers["cache-control"] == cache
    assert headers["etag"].startswith('"')
    assert not any(k.startswith("access-control-") for k in headers)


@pytest.mark.docker
def test_healthz(server):
    status, headers, body = server.get("/healthz")
    assert (status, body, headers["cache-control"]) == (200, b"ok\n", "no-store")


@pytest.mark.docker
@pytest.mark.parametrize("path", ["/v1/index.json", "/v1/images/f1.webp", "/v1/snapshot.tar.gz"])
def test_a_matching_etag_gets_304_with_the_cache_policy(server, path):
    _, first, _ = server.get(path)
    status, headers, body = server.get(path, **{"If-None-Match": first["etag"]})
    print(path, status, {k: headers.get(k) for k in ("etag", "cache-control")})
    assert (status, body) == (304, b"")
    assert headers["etag"] == first["etag"]
    assert headers["cache-control"] == first["cache-control"]


@pytest.mark.docker
def test_head_returns_headers_only(server):
    status, headers, body = server.request("HEAD", "/v1/index.json")
    assert (status, body, headers["content-type"]) == (200, b"", JSON)


@pytest.mark.docker
@pytest.mark.parametrize("path", [
    "/", "/v1/", "/v1/events/", "/v1/images/", "/v1/events", "/v1/events/nope.json", "/v1/nope.json",
    "/v1/images/misses.json", "/v1/images/nope.webp", "/v2/index.json", "/index.json", "/current/index.json",
    "/generations/", "/v1/../current/index.json",
])
def test_unknown_paths_and_directories_are_404(server, path):
    status, headers, body = server.get(path)
    assert status == 404, path
    assert headers["content-type"] == JSON
    assert b"not found" in body


@pytest.mark.docker
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@pytest.mark.parametrize("path", ["/v1/index.json", "/v1/nope", "/healthz"])
def test_only_get_and_head_are_allowed(server, method, path):
    status, headers, _ = server.request(method, path)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"


@pytest.mark.docker
def test_server_header_hides_the_version(server):
    assert server.get("/healthz")[1]["server"] == "nginx"


@pytest.mark.docker
def test_snapshot_extracts_to_the_served_files(server):
    _, _, blob = server.get("/v1/snapshot.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        assert len(members) == 7
        for m in members:
            status, _, body = server.get(f"/v1/{m.name}")
            assert (status, body) == (200, tar.extractfile(m).read()), m.name


@pytest.mark.docker
def test_etags_follow_content_across_generations(server, tmp_path):
    def etag(path: str) -> str:
        return server.get(path)[1]["etag"]

    paths = ("/v1/index.json", "/v1/images/f1.webp", "/v1/snapshot.tar.gz")
    before = {p: etag(p) for p in paths}
    assert {p: etag(p) for p in paths} == before  # stable between requests

    publish(generation(tmp_path, "2026-10-25T23:20:24Z"), server.root)  # identical data: no new generation
    assert {p: etag(p) for p in paths} == before

    publish(generation(tmp_path, "2026-10-26T06:00:00Z"), server.root)  # a new generation goes live
    status, _, body = server.get("/v1/index.json")
    assert status == 200 and b"2026-10-26T06:00:00Z" in body
    after = {p: etag(p) for p in paths}
    print("etags before", before, "after", after)
    assert after["/v1/index.json"] != before["/v1/index.json"]
    assert after["/v1/snapshot.tar.gz"] != before["/v1/snapshot.tar.gz"]
    assert after["/v1/images/f1.webp"] == before["/v1/images/f1.webp"]  # unchanged headshot stays cached
