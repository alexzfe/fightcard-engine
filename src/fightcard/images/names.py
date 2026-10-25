"""Fighter name normalisation and fuzzy matching across ufcstats, ESPN and Wikipedia."""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

THRESHOLD = 0.85
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def normalize_name(name: str | None) -> str:
    """Lowercase, strip accents and punctuation, split hyphens, drop Jr./Sr./II."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.replace("-", " ").replace("_", " ")
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name).lower()
    tokens = name.split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _token_match(a: str, b: str, threshold: float) -> bool:
    if fuzz.ratio(a, b) / 100 >= threshold:
        return True
    # "alex" vs "alexander": a prefix of at least three letters.
    short, long = sorted((a, b), key=len)
    return len(short) >= 3 and long.startswith(short)


def _tokens_match(x: list[str], y: list[str], threshold: float) -> bool:
    """Every token of the shorter name pairs with a distinct token of the longer one."""
    short, long = sorted((x, y), key=len)
    if len(short) < 2:
        return False
    used: set[int] = set()
    for s in short:
        for i, t in enumerate(long):
            if i not in used and _token_match(s, t, threshold):
                used.add(i)
                break
        else:
            return False
    return True


def names_match(
    name: str, candidate: str, threshold: float = THRESHOLD, nickname: str | None = None
) -> bool:
    """True if `candidate` plausibly names the same fighter as `name`.

    Order-insensitive; a nickname may stand in for the first name
    ("Sharabutdin Magomedov" with nickname "Shara Bullet" matches "Shara Magomedov").
    """
    a, b = normalize_name(name), normalize_name(candidate)
    if not a or not b:
        return False
    if a == b:
        return True
    tb = b.split()
    variants = [a.split()]
    surname = a.split()[-1:]
    for nick in normalize_name(nickname).split():
        variants.append([nick, *surname])
    return any(_tokens_match(v, tb, threshold) for v in variants)
