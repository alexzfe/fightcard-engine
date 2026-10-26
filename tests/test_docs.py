"""The docs describe what the code does: docs/CONTRACT.md is the /v1/ contract, docs/API.md explains it, and the
scrapped model-export automation is gone from model/README.md."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTRACT = (REPO / "docs" / "CONTRACT.md").read_text()
MODEL_README = (REPO / "model" / "README.md").read_text()
README = (REPO / "README.md").read_text()

ENDPOINTS = [
    ("/v1/index.json", "public, max-age=60"),
    ("/v1/events/{event_id}.json", "public, max-age=300"),
    ("/v1/fighters.json", "public, max-age=300"),
    ("/v1/images/{fighter_id}.webp", "public, max-age=86400"),
    ("/v1/schema.json", "public, max-age=300"),
    ("/v1/snapshot.tar.gz", "public, max-age=60"),
    ("/healthz", "no-store"),
]


def api_doc() -> str:
    path = REPO / "docs" / "API.md"
    assert path.is_file(), "docs/API.md is missing"
    return path.read_text()


@pytest.mark.parametrize("endpoint,cache", ENDPOINTS)
def test_contract_lists_every_endpoint_with_its_cache_policy(endpoint, cache):
    row = next((line for line in CONTRACT.splitlines() if line.startswith("|") and f"`{endpoint}`" in line), None)
    assert row is not None, endpoint
    assert cache in row, endpoint


@pytest.mark.parametrize("term", [
    "https://ufc-data.xlobax.com", "contract_version", "/v2/", "additive", "ETag", "304", "404", "405",
    "application/json; charset=utf-8", "snapshot", "deterministic", "2-space", "sorted", "trailing newline",
    "fightcard publish", "generations/", "schema.json",
])
def test_contract_covers(term):
    assert term in CONTRACT, term


def test_contract_drops_the_site_era_ownership_table():
    assert "Ownership (parallel build)" not in CONTRACT
    assert "feat/export" not in CONTRACT


def test_model_readme_drops_the_scrapped_export_automation():
    for stale in ("model-export", "deploy key", "release.yml", "Not in the current export yet"):
        assert stale not in MODEL_README, stale
    assert "runtime" in MODEL_README  # models get pulled at runtime in a later phase


@pytest.mark.parametrize("term", ["200", "304", "404", "405", "ETag", "If-None-Match", "Cache-Control",
                                  "/v2/", "snapshot.tar.gz", "contract_version"])
def test_api_doc_explains(term):
    assert term in api_doc(), term


def test_readme_links_the_api_doc():
    assert "(docs/API.md)" in README


# docs/SERVICE.md: how the engine runs on the VPS.

def service_doc() -> str:
    path = REPO / "docs" / "SERVICE.md"
    assert path.is_file(), "docs/SERVICE.md is missing"
    return path.read_text()


@pytest.mark.parametrize("term", [
    "one container", "compose.yml.j2", "06:00 UTC", "should_run.py", "flock", "FIGHTCARD_FETCH", "staging",
    "mem_limit", "stdout", "last good generation", "/srv/fightcard", "state/history/history.sqlite", "state/cache", "history ingest",
    "state/data", "seed", "history", "healthcheck: image",
])
def test_service_doc_covers(term):
    assert term in service_doc(), term


@pytest.mark.parametrize("term", [
    # Review fixes: timeouts and process groups, signals, the queued slot, staging's minute, health, backups, restart.
    "status=timeout", "process group", "nice", "SIGTERM", "STOP_GRACE", "status=queued", "FIGHTCARD_SCHEDULE_MINUTE",
    ":47", "fightcard-service health", "26 h", "2 h", "fightcard history backup", "pull-backup@ufc-data",
    "next slot",
    # Round 2: stale data doesn't fail the HEALTHCHECK; --staleness reports it.
    "--staleness",
])
def test_service_doc_covers_the_supervision(term):
    assert term in service_doc(), term


def test_service_doc_states_the_stage_timeouts():
    from fightcard.service.runner import TIMEOUTS
    doc = service_doc()
    for stage, seconds in TIMEOUTS.items():
        assert f"| {stage} | {seconds // 60} min |" in doc, stage


def test_readme_links_the_service_doc():
    assert "(docs/SERVICE.md)" in README
