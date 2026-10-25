import hashlib

import pytest
from fightcard.ufcstats.pow import Challenge, is_challenge, parse_challenge, solve


def test_detects_challenge_page(fixture_html):
    assert is_challenge(fixture_html("challenge.html"))


def test_real_pages_are_not_challenges(fixture_html):
    assert not is_challenge(fixture_html("upcoming.html"))
    assert not is_challenge(fixture_html("event_detail_upcoming.html"))


def test_parses_nonce_difficulty_and_path(fixture_html):
    assert parse_challenge(fixture_html("challenge.html")) == Challenge(
        nonce="efecdc18178fd36b", difficulty=2, path="/__c"
    )


def test_parse_rejects_page_without_nonce():
    with pytest.raises(ValueError):
        parse_challenge("<p>Checking your browser…</p>")


def test_solve_finds_first_n_with_required_zero_prefix():
    ch = Challenge(nonce="efecdc18178fd36b", difficulty=2, path="/__c")
    n = solve(ch)
    assert hashlib.sha256(f"{ch.nonce}:{n}".encode()).hexdigest().startswith("00")
    for smaller in range(n):
        assert not hashlib.sha256(f"{ch.nonce}:{smaller}".encode()).hexdigest().startswith("00")


def test_solve_respects_difficulty():
    ch = Challenge(nonce="abc", difficulty=3, path="/__c")
    assert hashlib.sha256(f"abc:{solve(ch)}".encode()).hexdigest().startswith("000")


def test_solve_gives_up_after_max_iterations():
    with pytest.raises(RuntimeError):
        solve(Challenge(nonce="abc", difficulty=64, path="/__c"), max_iterations=10)
