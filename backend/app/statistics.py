"""Small deterministic statistical guards shared by research diagnostics."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def safe_correlation(left: Any, right: Any, *, minimum_observations: int = 3,
                     method: str = "pearson") -> dict[str, float | str | int | None]:
    """Return a correlation only when it is mathematically defined.

    pandas/numpy correlation emits divide warnings for constant groups.  Filtering
    and checking finite, non-zero variance first makes unavailability explicit;
    it is deliberately never represented by zero.
    """
    pair = pd.DataFrame({"left": left, "right": right}).apply(pd.to_numeric, errors="coerce")
    pair = pair.replace([np.inf, -np.inf], np.nan).dropna()
    count = int(len(pair))
    if count < minimum_observations:
        return {"value": None, "reason": "insufficient_observations", "observations": count}
    if not np.isfinite(pair.left.var()) or not np.isfinite(pair.right.var()):
        return {"value": None, "reason": "nonfinite_variance", "observations": count}
    if pair.left.var() <= 0 or pair.right.var() <= 0:
        return {"value": None, "reason": "zero_variance", "observations": count}
    value = pair.left.corr(pair.right, method=method)
    if value is None or not np.isfinite(value):
        return {"value": None, "reason": "undefined", "observations": count}
    return {"value": float(value), "reason": None, "observations": count}
