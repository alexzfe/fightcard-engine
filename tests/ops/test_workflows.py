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


def test_ci_runs_on_pull_requests_and_pushes_to_main():
    text = read("ci.yml")
    assert re.search(r"^  pull_request:", text, re.M)
    assert re.search(r"^  push:\n    branches: \[main\]", text, re.M)


def test_ci_grants_no_permissions_by_default():
    assert re.search(r"^permissions: \{\}$", read("ci.yml"), re.M)


def test_ci_jobs_ask_only_for_read_access():
    grants = re.findall(r"^\s+(\w[\w-]*): (read|write)$", read("ci.yml"), re.M)
    assert grants and grants == [("contents", "read")] * len(grants)


def test_ci_test_job_runs_the_full_suite_from_the_locked_env():
    body = job(read("ci.yml"), "test")
    assert "run: uv sync --frozen" in body
    assert re.search(r"run: uv run pytest$", body, re.M)  # no paths: testpaths covers everything
    assert body.index("uv sync --frozen") < body.index("uv run pytest")


def test_ci_runs_actionlint():
    assert f"run: {ACTIONLINT}" in read("ci.yml")


def test_third_party_actions_are_pinned_to_commit_shas():
    files = list(WORKFLOWS.glob("*.yml"))
    assert files
    for path in files:
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text()):
            if ref.startswith("./"):
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
