"""Fighter rating (stub)."""

from __future__ import annotations

FORMAT_VERSION = None
COLUMNS: list = []
PARAMS: dict = {}


def k_factor(n):
    raise NotImplementedError


def expected(ra, rb):
    raise NotImplementedError


def decayed(r, days):
    raise NotImplementedError


def result_score(result, method):
    raise NotImplementedError


def rate(bouts, names=None):
    raise NotImplementedError


def records(fighters):
    raise NotImplementedError


def golden_cases():
    raise NotImplementedError


def history_ratings(conn, as_of=None):
    raise NotImplementedError


if __name__ == "__main__":
    raise NotImplementedError
