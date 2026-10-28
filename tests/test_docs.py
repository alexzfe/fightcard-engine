"""The docs describe what the code does: docs/CONTRACT.md is the /v1/ contract, docs/API.md explains it, and the
scrapped model-export automation is gone from model/README.md."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "publish"))
from test_v1_enums import V1_ENUMS, enum_field  # noqa: E402

ENUM_FIELDS = sorted({enum_field(where) for where in V1_ENUMS})  # "Fight.finish_tier", "ModelInfo.tiers", ...
CONTRACT = (REPO / "docs" / "CONTRACT.md").read_text()
MODEL_README = (REPO / "model" / "README.md").read_text()
README = (REPO / "README.md").read_text()

ENDPOINTS = [
    ("/v1/index.json", "public, max-age=60"),
    ("/v1/events/{event_id}.json", "public, max-age=300"),
    ("/v1/fighters.json", "public, max-age=300"),
    ("/v1/images/{fighter_id}.webp", "public, max-age=86400"),
    ("/v1/rankings.json", "public, max-age=300"),
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
    "fightcard publish", "generations/", "schema.json", "rankings.json", '"1.1"', '"1.2"', '"1.3"', "p4p_rank", "as_of",
    # Review fixes: a consumer's allowlist of files broke on rankings.json; the rank/elo guarantee needs a history run.
    "fields and files they don't know", "announced in a minor version", "holds after a history run",
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
                                  "/v2/", "snapshot.tar.gz", "contract_version", "rankings.json"])
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


def test_service_doc_says_to_seed_the_store_fully_before_the_first_run():
    # PR #3: scrape backfills at most BACKFILL_PER_RUN old events a run; score stays strict on the rest until then.
    from fightcard.ufcstats.stage import BACKFILL_PER_RUN
    seeding = service_doc().split("## Seeding a new volume")[1].split("\n## ")[0]
    for term in ("before the first run", "fightcard history ingest", f"at most {BACKFILL_PER_RUN}", "backfill_skips"):
        assert term in seeding, term


def test_service_doc_records_the_release_order():
    """The first run of an engine that publishes rankings.json breaks a site whose fetch_v1 doesn't know the file."""
    order = service_doc().split("## Release order")[1].split("\n## ")[0]
    for term in ("fetch_v1", "rankings.json", "feat/rankings", "main", "before this engine is deployed",
                 "every v1 refresh fails", "rollback"):
        assert term in order, term


def test_service_doc_says_a_new_file_no_longer_needs_the_release_order():
    order = service_doc().split("## Release order")[1].split("\n## ")[0]
    assert "The same order applies to any file added" not in order
    assert "skips" in order and "alexzfe/fight-card-website#12" in order and "feat/contract-checks" not in order


def test_release_order_reads_as_history():
    """The release order was for rankings.json; the allowlist it describes is gone, and this doc's engine changes no
    behaviour, so nothing here gates its deploy."""
    order = " ".join(service_doc().split("## Release order")[1].split("\n## ")[0].split())
    assert "allowlists" not in order and "refuses any other member" not in order
    assert "allowlisted" in order and "refused any other member" in order
    assert "Deploy this doc's engine" not in order
    assert "This holds once site PR #12 is on the site's `main`." in order


def test_service_doc_states_the_stage_timeouts():
    from fightcard.service.runner import TIMEOUTS
    doc = service_doc()
    for stage, seconds in TIMEOUTS.items():
        assert f"| {stage} | {seconds // 60} min |" in doc, stage


def test_readme_links_the_service_doc():
    assert "(docs/SERVICE.md)" in README


# Phase 5: the engine pulls the model research publishes.

def model_section() -> str:
    return service_doc().split("## The model")[1].split("\n## ")[0]


@pytest.mark.parametrize("term", [
    "FIGHTCARD_MODEL_URL", "FIGHTCARD_MODEL_PIN", "FIGHTCARD_MODEL_DIR", "fightcard model pull", "latest.json",
    "sha256", "load_bundle", "examples.json", "low_max", "medium_max", "held", "rollback", "state/model/current",
    "last 3", "first boot", "%2B", "unreachable", "exit 0", "models.xlobax.com", "no-op", "does not fail",
    "model.stale_fighters",
])
def test_service_doc_covers_the_model_pull(term):
    assert term in model_section(), term


@pytest.mark.parametrize("term", [
    ".held-<version>",       # the marker: a held or crashed version isn't fetched again until something changes
    "same files",            # ... what has to change
    "as_of",                 # a latest.json older than the running model is held (no silent downgrade)
    "older",
    "Redirects",             # only http(s) is followed
    "ERROR",                 # an unusable pin is loud
    "baked model is newer",  # an upgraded image's model replaces an older current
    "--fallback-model-dir",  # a current that won't load is as good as none
    ".current-",             # swept with .tmp-*
])
def test_service_doc_covers_the_review_fixes(term):
    assert term in " ".join(model_section().split()), term


def test_service_doc_no_longer_rolls_back_through_latest_json():
    section = model_section()
    assert "point `latest.json` back" not in section
    assert "only way" in section and "FIGHTCARD_MODEL_PIN" in section


def test_service_doc_says_score_reads_the_state_model_only_when_a_pull_is_configured():
    section = model_section()
    assert "only when a pull is configured" in section


def test_service_doc_shows_the_pull_in_the_run_and_the_volume():
    doc = service_doc()
    assert "gate → scrape → segments → images → model pull → score → publish" in doc
    assert any(line.startswith("| `state/model/") for line in doc.splitlines())


def test_service_doc_tells_how_to_pin_and_roll_back():
    section = model_section()
    assert "FIGHTCARD_MODEL_PIN=<version>" in section
    assert "kept" in section and "ls /srv/fightcard/state/model" in section


def test_contract_documents_the_model_fields_and_the_pull():
    assert "`index.model.stale_fighters`" in CONTRACT
    for field in ("model.version", "model.as_of"):
        assert field in CONTRACT, field
    row = next((line for line in CONTRACT.splitlines() if line.startswith("| `model pull`")), None)
    assert row is not None and "FIGHTCARD_MODEL_URL" in row and "state/model" in row


def test_contract_versioning_says_what_1_1_added():
    section = CONTRACT.split("### Versioning")[1].split("###")[0]
    assert '`"1.1"`' in section
    for added in ("Result.outcome", "elo", "best_win", "index.model.stale_fighters"):
        assert added in section, added
    assert "Added without a new" not in CONTRACT  # it is 1.1 now


def test_contract_versioning_says_1_2_added_rankings_json():
    section = CONTRACT.split("### Versioning")[1].split("###")[0]
    line = next((line for line in section.splitlines() if line.strip().startswith('- `"1.2"`')), None)
    assert line is not None and "rankings.json" in line
    one_one = next(line for line in section.splitlines() if line.strip().startswith('- `"1.1"`'))
    assert "rankings.json" not in one_one  # 1.1 is the model work's, and stays as it was
    assert '`"1.1"` added `rankings.json`' not in CONTRACT and '`"1.2"` added `rankings.json`' in CONTRACT


def test_contract_versioning_says_1_3_added_fight_elo():
    section = CONTRACT.split("### Versioning")[1].split("###")[0]
    line = next((line for line in section.splitlines() if line.strip().startswith('- `"1.3"`')), None)
    assert line is not None and "Fight.elo" in line


@pytest.mark.parametrize("term", ["Fight.elo", "red_pre", "red_delta", "blue_pre", "blue_delta", "red_p4p_rank",
                                  "blue_p4p_rank", "the day before", "today's shift"])
def test_contract_documents_fight_elo(term):
    layout = CONTRACT.split("## data/ layout")[1].split("\n## ")[0]
    assert term in layout, term


def test_score_row_names_fight_elo():
    row = next(line for line in CONTRACT.splitlines() if line.startswith("| `score`"))
    assert "`Fight.elo`" in row


def test_api_doc_explains_fight_elo():
    doc = api_doc()
    assert "Fight.elo" in doc or "`elo`" in doc
    assert '`"1.3"`' in doc and "red_pre" in doc


@pytest.mark.parametrize("term", ["Fight.elo", "the day before", "a point", "--as-of", "tournament"])
def test_elo_doc_explains_fight_time_elo(term):
    assert term in (REPO / "docs" / "ELO.md").read_text(), term


def test_contract_says_fight_elo_can_change_a_completed_event_file():
    """Fight.elo is recomputed on today's shift each run, so a completed card's file (ETag, Last-Modified,
    snapshot) can change when the shift moves."""
    field = CONTRACT.split("- `Fight.elo` (")[1].split("\n- `")[0].split("\n### ")[0]
    for term in ("`ETag`", "`Last-Modified`", "snapshot"):
        assert term in field, term


def test_api_doc_says_fight_elo_can_change_a_completed_event_file():
    paragraph = api_doc().split("A completed fight's `elo`")[1].split("\n\n")[0]
    assert "`ETag`" in paragraph and "completed" in paragraph


def test_elo_doc_says_as_of_includes_the_day():
    """`rating_bouts(as_of)` is inclusive: the rank going into D is `--as-of` the day before, and `--as-of D`
    already counts D's bouts."""
    section = (REPO / "docs" / "ELO.md").read_text().split("### At fight time")[1].split("\n## ")[0]
    assert "up to and including" in section


def test_api_doc_says_rankings_json_appears_from_1_2():
    row = next(line for line in api_doc().splitlines() if line.startswith("| `/v1/rankings.json`"))
    assert '`"1.2"`' in row and '`"1.1"`' not in row


def test_release_order_names_the_contract_a_rollback_goes_back_to():
    """The engine before this one already publishes 1.1 (the model work), only without rankings.json."""
    order = service_doc().split("## Release order")[1].split("\n## ")[0]
    assert '`"1.1"`' in order and "contract `\"1\"`" not in order


def test_api_doc_names_the_model_fields():
    for field in ("model.version", "model.as_of", "model.stale_fighters"):
        assert field in api_doc(), field


def test_model_readme_says_re_exporting_is_automated_and_hand_export_still_works():
    section = MODEL_README.split("## Re-exporting")[1]
    for term in ("research", "R2", "latest.json", "FIGHTCARD_MODEL_URL", "docs/SERVICE.md", "by hand"):
        assert term in section, term
    assert "By hand for now" not in section
    assert "A later phase stops vendoring" not in section


def test_readme_mentions_the_model_pull():
    assert "fightcard model pull" in README


def test_contract_orders_active_by_the_unrounded_rating_and_says_equal_elo_may_not_be_in_id_order():
    line = " ".join(CONTRACT.split("- `active`:")[1].split("\n- ")[0].split())
    assert "unrounded" in line and "best `elo` first" not in line
    assert "equal `elo`" in line and "id order" in line


def test_contract_says_the_site_skips_unknown_files_so_a_new_file_needs_no_release_order():
    """The site's fetch_v1 skips files outside the /v1/ layout (site PR alexzfe/fight-card-website#12), so the engine can ship
    a new file first."""
    section = CONTRACT.split("### Versioning")[1].split("###")[0]
    assert "strict allowlist" not in section
    assert "`scripts/fetch_v1.py` skips" in section


def test_contract_says_enum_values_are_closed_in_v1():
    section = CONTRACT.split("### Versioning")[1].split("###")[0]
    bullet = " ".join(section.split("- Enum values are closed within `/v1/`")[1].split("\n- ")[0].split())
    for term in ("a new value in an existing enum is a breaking change and ships as `/v2/`",
                 "`tests/publish/test_v1_enums.py`"):
        assert term in bullet, term


@pytest.mark.parametrize("field", ENUM_FIELDS)
def test_contract_names_every_pinned_enum(field):
    bullet = CONTRACT.split("- Enum values are closed within `/v1/`")[1].split("\n- ")[0]
    assert f"`{field}`" in bullet, field


def test_api_doc_says_enum_values_are_closed():
    section = api_doc().split("## Versioning")[1].split("\n## ")[0]
    sentence = " ".join(section.split("The values of an existing enum")[1].split("strictly.")[0].split())
    assert "a new value would go to `/v2/`" in sentence


@pytest.mark.parametrize("field", ENUM_FIELDS)
def test_api_doc_names_every_pinned_enum(field):
    """By the name a client sees: `finish_tier`, a past fight's `result`, the keys of `index.model.tiers`."""
    section = api_doc().split("## Versioning")[1].split("\n## ")[0]
    sentence = " ".join(section.split("The values of an existing enum")[1].split("strictly.")[0].split())
    name = field.split(".")[1]
    assert f"`{name}`" in sentence or f"`index.model.{name}`" in sentence, field


def test_api_doc_tells_consumers_to_ignore_unknown_files_too():
    section = api_doc().split("## Versioning")[1].split("\n## ")[0]
    assert "ignore fields and files they don't know" in section
