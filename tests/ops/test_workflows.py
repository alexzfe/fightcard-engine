"""Structural checks on the workflow files. No YAML parser is available in the env, so these read the text;
actionlint covers the syntax and expression types."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ACTIONLINT = "uvx --from actionlint-py==1.7.12.25 actionlint"


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def job(text: str, name: str) -> str:
    """The body of one job: from `  <name>:` to the next top-level job key or the end."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    assert match, f"no job {name!r}"
    return match.group(1)


DEPLOY_APP = "alexzfe/infra/.github/workflows/deploy-app.yml@main"  # first-party, as in infra's app-release template
IMAGE = "ghcr.io/alexzfe/fightcard-engine"


def test_release_runs_on_pull_requests_pushes_to_main_and_version_tags():
    text = read("release.yml")
    assert re.search(r"^  pull_request:", text, re.M)
    assert re.search(r"^  push:\n    branches: \[main\]\n    tags: \[\"v\*\"\]", text, re.M)


def test_ci_is_merged_into_release():
    assert not (WORKFLOWS / "ci.yml").exists()  # one workflow for PRs and main, not two running the same tests


@pytest.mark.parametrize("name", ["release.yml", "build.yml"])
def test_workflows_grant_no_permissions_by_default(name):
    assert re.search(r"^permissions: \{\}$", read(name), re.M)


def grants(body: str) -> dict[str, str]:
    return dict(re.findall(r"^      (\w[\w-]*): (read|write)$", body, re.M))


@pytest.mark.parametrize("name,expected", [
    ("test", {"contents": "read"}),
    ("docker", {"contents": "read"}),
    ("build", {"contents": "read", "packages": "write"}),
    ("staging", {}),
    ("prod", {}),
    ("release", {"contents": "write"}),
])
def test_each_release_job_gets_only_what_it_needs(name, expected):
    assert grants(job(read("release.yml"), name)) == expected


def test_build_job_can_only_read_and_push_packages():
    assert grants(job(read("build.yml"), "build")) == {"contents": "read", "packages": "write"}


def test_test_job_runs_the_full_suite_from_the_locked_env_and_actionlint():
    body = job(read("release.yml"), "test")
    assert "if: github.ref_type != 'tag'" in body
    assert "run: uv sync --frozen" in body
    assert re.search(r"run: uv run pytest$", body, re.M)  # no paths: testpaths covers everything
    assert body.index("uv sync --frozen") < body.index("uv run pytest")
    assert f"run: {ACTIONLINT}" in body


def test_docker_job_runs_the_container_tests():
    body = job(read("release.yml"), "docker")
    assert "if: github.ref_type != 'tag'" in body
    assert "run: uv sync --frozen" in body
    assert re.search(r"run: uv run pytest -m docker$", body, re.M)


def test_main_builds_the_image_after_both_test_jobs():
    body = job(read("release.yml"), "build")
    assert "if: github.ref == 'refs/heads/main'" in body
    assert "needs: [test, docker]" in body
    assert "uses: ./.github/workflows/build.yml" in body
    assert "ref: ${{ github.sha }}" in body


def test_build_workflow_pushes_the_sha_tagged_image():
    text = read("build.yml")
    assert re.search(r"^  workflow_call:", text, re.M)
    assert f"IMAGE: {IMAGE}" in text
    assert "tags: ${{ env.IMAGE }}:${{ inputs.ref }}" in text
    assert "ref: ${{ inputs.ref }}" in text
    assert "push: true" in text


# deploy-app smoke-tests `url`; nginx answers 404 for `/`, so it must be the health path.
@pytest.mark.parametrize("name,env,url,condition", [
    ("staging", "staging", "https://ufc-data-staging.xlobax.com/healthz", "needs: build"),
    ("prod", "prod", "https://ufc-data.xlobax.com/healthz", "if: github.ref_type == 'tag'"),
])
def test_deploys_go_through_infras_deploy_app(name, env, url, condition):
    body = job(read("release.yml"), name)
    assert f"uses: {DEPLOY_APP}" in body
    assert "app: ufc-data" in body
    assert f"env: {env}" in body
    assert "tag: ${{ github.sha }}" in body  # prod runs the image main already built
    assert f"url: {url}" in body
    assert condition in body
    assert "secrets: inherit" in body


def test_a_github_release_follows_the_prod_deploy():
    body = job(read("release.yml"), "release")
    assert "needs: prod" in body
    assert 'gh release create "$GITHUB_REF_NAME"' in body


def test_checkouts_do_not_persist_credentials():
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        for match in re.finditer(r"uses: actions/checkout@\S+.*\n((?:        .*\n)*)", text):
            assert "persist-credentials: false" in match.group(1), path.name


def test_third_party_actions_are_pinned_to_commit_shas():
    files = list(WORKFLOWS.glob("*.yml"))
    assert files
    for path in files:
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text()):
            if ref.startswith("./") or ref == DEPLOY_APP:
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{path.name}: {ref}"


def actionlint() -> str | None:
    exe = os.environ.get("ACTIONLINT") or shutil.which("actionlint")
    if exe and subprocess.run([exe, "--version"], capture_output=True).returncode == 0:
        return exe
    return None


@pytest.mark.skipif(actionlint() is None, reason="actionlint not available (set ACTIONLINT=/path)")
def test_actionlint_passes():
    files = sorted(str(p) for p in WORKFLOWS.glob("*.yml"))
    assert files
    result = subprocess.run([actionlint(), *files], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
