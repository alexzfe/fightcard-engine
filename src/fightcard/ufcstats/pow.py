"""ufcstats proof-of-work challenge: detect, parse, solve.

The challenge page finds the smallest n where sha256(nonce + ":" + n) hex starts
with `difficulty` zeros, then POSTs `nonce=…&n=…` to `path` (fixture:
tests/fixtures/ufcstats/challenge.html).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

CHALLENGE_MARKERS = ("Checking your browser", '"/__c"')

_NONCE = re.compile(r'var\s+nonce\s*=\s*"([0-9a-fA-F]+)"')
_DIFFICULTY = re.compile(r"new\s+Array\(\s*(\d+)\s*\+\s*1\s*\)\.join\('0'\)")
_PATH = re.compile(r"xhr\.open\(\s*'POST'\s*,\s*\"([^\"]+)\"")


@dataclass(frozen=True)
class Challenge:
    nonce: str
    difficulty: int
    path: str


def is_challenge(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


def parse_challenge(html: str) -> Challenge:
    nonce = _NONCE.search(html)
    if not nonce:
        raise ValueError("challenge page has no nonce")
    difficulty = _DIFFICULTY.search(html)
    path = _PATH.search(html)
    return Challenge(
        nonce=nonce[1],
        difficulty=int(difficulty[1]) if difficulty else 2,
        path=path[1] if path else "/__c",
    )


def solve(challenge: Challenge, max_iterations: int = 10_000_000) -> int:
    target = "0" * challenge.difficulty
    for n in range(max_iterations):
        if hashlib.sha256(f"{challenge.nonce}:{n}".encode()).hexdigest().startswith(target):
            return n
    raise RuntimeError(f"no solution within {max_iterations} iterations")
