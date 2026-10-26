"""The repo root is the project: `uv run pytest` there runs every suite, the package imports as `fightcard`,
and the CLI offers the pipeline stages."""

import subprocess
import sys
from pathlib import Path

import fightcard

REPO = Path(__file__).resolve().parents[1]


def pytest_at_root(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
                          cwd=REPO, capture_output=True, text=True)


def test_every_suite_collects_from_the_repo_root():
    result = pytest_at_root("--collect-only", "-q")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    for suite in ("images", "ops", "score", "segments", "ufcstats"):
        assert f"tests/{suite}/" in result.stdout, suite


def test_ops_scripts_run_from_the_repo_root():
    result = pytest_at_root("-q", "tests/ops/test_should_run.py", "tests/ops/test_stale_issue.py")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]


def test_score_tests_find_the_vendored_model_at_the_repo_root():
    result = pytest_at_root("-q", "tests/score/test_score_parity.py", "tests/score/test_score_stage.py")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]


def test_package_imports_as_fightcard_from_src():
    assert Path(fightcard.__file__).resolve().parent == REPO / "src" / "fightcard"


def test_cli_help_lists_the_stages():
    exe = Path(sys.executable).with_name("fightcard")
    result = subprocess.run([str(exe), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for stage in ("scrape", "segments", "images", "score", "history"):
        assert stage in result.stdout, stage
