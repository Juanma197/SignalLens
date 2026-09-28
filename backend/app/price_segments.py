"""Deterministic, read-only historical price segmentation.

Boundaries describe evidence in stored provider rows; they neither repair rows
nor assert that an unrecorded corporate action occurred.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SegmentPolicy:
    material_ratio: float = 10.0
    raw_adjusted_disagreement_ratio: float = 2.0
    action_window_days: int = 7
    zero_volume_requires_discontinuity: bool = True


def _ratio(left: Any, right: Any) -> float | None:
    left, right = float(left), float(right)
    if not np.isfinite(left) or not np.isfinite(right) or left <= 0 or right <= 0:
        return None
    return float(max(left / right, right / left))


def detect_price_segments(prices: pd.DataFrame, actions: pd.DataFrame | None = None, *,
                          policy: SegmentPolicy = SegmentPolicy()) -> pd.DataFrame:
    """Return one deterministic unresolved boundary per adjacent session pair."""
    frame = prices.copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    frame = frame.loc[frame["status"].eq("available")].sort_values(
        ["qualified_symbol", "trading_date", "retrieved_at"]
    )
    # Ambiguous duplicate keys are not used to infer an adjacent-session cause.
    frame = frame.loc[~frame.duplicated(["qualified_symbol", "trading_date"], keep=False)]
    action_frame = actions.copy() if actions is not None else pd.DataFrame()
    if not action_frame.empty:
        action_frame["ex_date"] = pd.to_datetime(action_frame["ex_date"])
    rows: list[dict[str, Any]] = []
    for symbol, history in frame.groupby("qualified_symbol", sort=True):
        records = list(history.itertuples(index=False))
        for left, right in zip(records, records[1:]):
            adjusted_ratio = _ratio(left.adjusted_close, right.adjusted_close)
            raw_ratio = _ratio(left.close, right.close) if "close" in frame else adjusted_ratio
            material_raw = raw_ratio is not None and raw_ratio >= policy.material_ratio
            material_adjusted = adjusted_ratio is not None and adjusted_ratio >= policy.material_ratio
            if not (material_raw or material_adjusted):
                continue
            right_date = pd.Timestamp(right.trading_date)
            nearby_action = False
            if not action_frame.empty and {"qualified_symbol", "ex_date"} <= set(action_frame):
                nearby_action = bool(action_frame.loc[
                    action_frame["qualified_symbol"].astype(str).eq(str(symbol)), "ex_date"
                ].between(right_date - pd.Timedelta(days=policy.action_window_days),
                          right_date + pd.Timedelta(days=policy.action_window_days)).any())
            reasons: list[str] = []
            if material_raw:
                reasons.append("raw_price_discontinuity")
            if material_adjusted:
                reasons.append("adjusted_price_discontinuity")
            if raw_ratio and adjusted_ratio and max(raw_ratio, adjusted_ratio) / min(raw_ratio, adjusted_ratio) >= policy.raw_adjusted_disagreement_ratio:
                reasons.append("raw_adjusted_disagreement")
            volumes = (getattr(left, "volume", None), getattr(right, "volume", None))
            if policy.zero_volume_requires_discontinuity and any(value == 0 for value in volumes):
                reasons.append("zero_volume_discontinuity")
            if not nearby_action:
                reasons.append("missing_action_evidence")
            # A raw split reflected by a continuous adjusted series and a recorded
            # action is resolved. Everything else remains a conservative boundary.
            unresolved = material_adjusted or not nearby_action or "raw_adjusted_disagreement" in reasons or "zero_volume_discontinuity" in reasons
            if unresolved:
                rows.append({"qualified_symbol": str(symbol), "left_date": pd.Timestamp(left.trading_date),
                    "boundary_date": right_date, "raw_ratio": raw_ratio,
                    "adjusted_ratio": adjusted_ratio, "action_within_window": nearby_action,
                    "reason_codes": tuple(reasons), "provenance": {
                        "left_source": str(getattr(left, "source", "unknown")),
                        "right_source": str(getattr(right, "source", "unknown")),
                        "left_retrieved_at": str(getattr(left, "retrieved_at", "unknown")),
                        "right_retrieved_at": str(getattr(right, "retrieved_at", "unknown")),
                    }})
    return pd.DataFrame(rows, columns=["qualified_symbol", "left_date", "boundary_date",
        "raw_ratio", "adjusted_ratio", "action_within_window", "reason_codes", "provenance"])


def crosses_boundary(boundaries: pd.DataFrame, symbol: str, start: Any, end: Any) -> tuple[str, ...]:
    """Return reasons for boundaries in ``(start, end]`` (window-aware)."""
    if boundaries.empty:
        return ()
    selected = boundaries.loc[boundaries["qualified_symbol"].eq(symbol)
        & boundaries["boundary_date"].gt(pd.Timestamp(start))
        & boundaries["boundary_date"].le(pd.Timestamp(end))]
    return tuple(sorted({reason for reasons in selected["reason_codes"] for reason in reasons}))


def boundary_aggregates(boundaries: pd.DataFrame, regions: dict[str, str]) -> dict[str, dict[str, int]]:
    reasons, region_counts = Counter(), Counter()
    for row in boundaries.itertuples(index=False):
        reasons.update(row.reason_codes)
        region_counts[regions.get(row.qualified_symbol, "UNKNOWN")] += 1
    return {"by_reason": dict(sorted(reasons.items())), "by_region": dict(sorted(region_counts.items()))}
