"""The export's own examples.json, re-scored: the check that a bundle scores here exactly as research exported it."""

from __future__ import annotations

import numpy as np

from fightcard.score.bundle import Bundle
from fightcard.score.features import bout_row

MIN_EXAMPLES = 5
ATOL = 1e-9


def parity_failures(bundle: Bundle, examples: object) -> list[str]:
    """Why `bundle` doesn't reproduce `examples` (one entry per example that moved); empty when it does."""
    if not isinstance(examples, list) or len(examples) < MIN_EXAMPLES:
        return [f"examples.json must be a list of at least {MIN_EXAMPLES} bouts"]
    try:
        rows = [bout_row(bundle.snapshot, bundle.spec, e["red"], e["blue"], e["date"], e["context"])
                for e in examples]
        p = bundle.predict(rows)
        failures = []
        for i, (got, e) in enumerate(zip(p, examples)):
            if not np.isclose(got, e["p_finish"], rtol=0, atol=ATOL):
                failures.append(f"example {i}: p_finish {float(got)!r} != exported {e['p_finish']!r}")
            elif bundle.tier(float(got)) != e["tier"]:
                failures.append(f"example {i}: tier {bundle.tier(float(got))} != exported {e['tier']}")
    except Exception as e:  # noqa: BLE001 - a candidate's own data decides what raises (lightgbm, numpy, pandas)
        return [f"examples.json can't be scored: {type(e).__name__}: {e}"]
    return failures
