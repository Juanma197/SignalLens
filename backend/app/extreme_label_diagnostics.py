"""Bounded, read-only provenance diagnostics for Milestone 16 labels.

The database entry point deliberately delegates panel construction to
``walk_forward_evidence``.  Consequently this report cannot silently diagnose
a different horizon, calendar, eligibility set, or point-in-time boundary than
the research score.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, _frame, _same_file, _validate_schema, fingerprint
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import EvidencePolicy, walk_forward_evidence

NEIGHBOUR_RADIUS = 2
MAX_AFFECTED = 25


def _ratio(left: float, right: float) -> float | None:
    if not np.isfinite(left) or not np.isfinite(right) or left <= 0 or right <= 0:
        return None
    return float(max(left / right, right / left))


def _return(entry: float, exit_value: float) -> float | None:
    if not np.isfinite(entry) or not np.isfinite(exit_value) or entry == 0:
        return None
    return float(exit_value / entry - 1)


def _iso(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def diagnose_label_provenance(
    predictions: pd.DataFrame,
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    policy: EvidencePolicy = EvidencePolicy(),
    affected_limit: int = MAX_AFFECTED,
) -> dict[str, Any]:
    """Diagnose only policy-selected labels, returning bounded sanitized data."""
    if not 1 <= affected_limit <= MAX_AFFECTED:
        raise ValueError(f"affected_limit must be between 1 and {MAX_AFFECTED}")
    labels = predictions.copy()
    extreme = pd.to_numeric(labels["forward_return"], errors="coerce").abs().gt(
        policy.extreme_absolute_return
    )
    near_zero = pd.to_numeric(labels["entry_adjusted_close"], errors="coerce").abs().le(
        policy.near_zero_adjusted_close
    )
    affected = labels.loc[extreme | near_zero].copy()
    affected["_magnitude"] = pd.to_numeric(affected["forward_return"], errors="coerce").abs()
    affected = affected.sort_values(
        ["_magnitude", "qualified_symbol", "vintage_date"], ascending=[False, True, True]
    )

    frame = prices.copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    frame = frame.loc[frame["status"].eq("available")].sort_values(
        ["qualified_symbol", "trading_date", "retrieved_at"]
    )
    duplicate_keys = frame.duplicated(["qualified_symbol", "trading_date"], keep=False)
    action_frame = actions.copy()
    if not action_frame.empty:
        action_frame["ex_date"] = pd.to_datetime(action_frame["ex_date"])

    output: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    for label in affected.head(affected_limit).itertuples(index=False):
        symbol = str(label.qualified_symbol)
        entry_date, exit_date = pd.Timestamp(label.vintage_date), pd.Timestamp(label.label_date)
        history = frame.loc[frame["qualified_symbol"].eq(symbol)].copy()
        unique = history.drop_duplicates("trading_date", keep=False).sort_values("trading_date")
        by_date = unique.set_index("trading_date")
        entry = by_date.loc[entry_date] if entry_date in by_date.index else None
        exit_row = by_date.loc[exit_date] if exit_date in by_date.index else None
        if entry is None or exit_row is None:
            # This should be unreachable because labels come from the same panel.
            continue
        pairs: list[dict[str, Any]] = []
        rows = list(unique.itertuples(index=False))
        for left, right in zip(rows, rows[1:]):
            pairs.append({
                "left": pd.Timestamp(left.trading_date), "right": pd.Timestamp(right.trading_date),
                "raw_ratio": _ratio(float(left.close), float(right.close)),
                "adjusted_ratio": _ratio(float(left.adjusted_close), float(right.adjusted_close)),
            })
        largest_raw = max(pairs, key=lambda item: item["raw_ratio"] or -1, default=None)
        largest_adjusted = max(pairs, key=lambda item: item["adjusted_ratio"] or -1, default=None)
        candidates = [item for item in (largest_raw, largest_adjusted) if item]
        discontinuity = max(
            candidates,
            key=lambda item: max(item["raw_ratio"] or -1, item["adjusted_ratio"] or -1),
            default=None,
        )
        discontinuity_date = discontinuity["right"] if discontinuity else entry_date
        position = "before" if discontinuity_date <= entry_date else (
            "inside" if discontinuity_date <= exit_date else "after"
        )
        center = next((i for i, row in enumerate(rows)
                       if pd.Timestamp(row.trading_date) == discontinuity_date), 0)
        neighbours = rows[max(0, center - NEIGHBOUR_RADIUS):center + NEIGHBOUR_RADIUS + 1]
        window = unique.loc[unique["trading_date"].between(entry_date, exit_date)]
        symbol_actions = action_frame.loc[
            action_frame["qualified_symbol"].astype(str).eq(symbol)
        ] if not action_frame.empty else action_frame
        proximity = {
            str(days): int(symbol_actions["ex_date"].between(
                discontinuity_date - pd.Timedelta(days=days),
                discontinuity_date + pd.Timedelta(days=days),
            ).sum()) if not symbol_actions.empty else 0
            for days in (7, 30, 90)
        }
        raw_return = _return(float(entry["close"]), float(exit_row["close"]))
        adjusted_return = _return(float(entry["adjusted_close"]), float(exit_row["adjusted_close"]))
        raw_jump = (largest_raw or {}).get("raw_ratio") or 1
        adjusted_jump = (largest_adjusted or {}).get("adjusted_ratio") or 1
        reasons: list[str] = []
        if abs(float(entry["adjusted_close"])) <= policy.near_zero_adjusted_close:
            reasons.append("near_zero_denominator_amplification")
        if raw_jump >= policy.extreme_absolute_return:
            reasons.append("raw_price_split_like_discontinuity")
        if adjusted_jump >= policy.extreme_absolute_return:
            reasons.append("adjusted_price_discontinuity")
        pair_disagreement = any(
            max(item["raw_ratio"] or 1, item["adjusted_ratio"] or 1)
            / max(1, min(item["raw_ratio"] or 1, item["adjusted_ratio"] or 1)) >= 2
            for item in pairs
        )
        if pair_disagreement:
            reasons.append("raw_adjusted_disagreement")
        if raw_jump >= policy.extreme_absolute_return and adjusted_jump >= policy.extreme_absolute_return and not proximity["90"]:
            reasons.append("possible_ticker_reuse")
        if raw_jump >= policy.extreme_absolute_return and adjusted_jump >= policy.extreme_absolute_return and not proximity["30"]:
            reasons.append("possible_missing_split_adjustment")
        if max(raw_jump, adjusted_jump) >= policy.extreme_absolute_return and len(neighbours) >= 3:
            before_after = [float(row.adjusted_close) for row in neighbours if pd.Timestamp(row.trading_date) != discontinuity_date]
            if before_after and adjusted_jump >= policy.extreme_absolute_return:
                reasons.append("isolated_malformed_observation")
        if not reasons:
            reasons.append("insufficient_evidence")
        # Preserve several plausible classifications; these are diagnostics, not causes.
        reasons = list(dict.fromkeys(reasons))
        classification_counts.update(reasons)
        region_counts[str(label.region)] += 1
        output.append({
            "qualified_symbol": symbol, "region": str(label.region),
            "currency": str(entry["currency"]), "vintage_date": _iso(entry_date),
            "entry_date": _iso(entry_date), "label_date": _iso(exit_date),
            "exit_date": _iso(exit_date),
            "entry_raw_close": float(entry["close"]), "exit_raw_close": float(exit_row["close"]),
            "entry_adjusted_close": float(entry["adjusted_close"]),
            "exit_adjusted_close": float(exit_row["adjusted_close"]),
            "raw_return": raw_return, "adjusted_return": adjusted_return,
            "entry_volume": int(entry["volume"]), "exit_volume": int(exit_row["volume"]),
            "label_window_adjusted_close": {
                "minimum": float(window["adjusted_close"].min()),
                "maximum": float(window["adjusted_close"].max()),
            },
            "largest_adjacent_session_ratios": {
                "raw": None if largest_raw is None else {
                    "ratio": largest_raw["raw_ratio"], "from": _iso(largest_raw["left"]),
                    "to": _iso(largest_raw["right"]),
                },
                "adjusted": None if largest_adjusted is None else {
                    "ratio": largest_adjusted["adjusted_ratio"],
                    "from": _iso(largest_adjusted["left"]),
                    "to": _iso(largest_adjusted["right"]),
                },
            },
            "largest_discontinuity_date": _iso(discontinuity_date),
            "discontinuity_relative_to_label_window": position,
            "corporate_action_proximity_counts": proximity,
            "retrieval_timestamp": {
                "entry": pd.Timestamp(entry["retrieved_at"]).isoformat(),
                "exit": pd.Timestamp(exit_row["retrieved_at"]).isoformat(),
            },
            "source_identifier": {"entry": str(entry["source"]),
                                  "exit": str(exit_row["source"])},
            "duplicate_key_status": {
                "symbol_has_duplicate_price_key": bool(duplicate_keys.loc[history.index].any()),
                "entry_key_count": int((history["trading_date"] == entry_date).sum()),
                "exit_key_count": int((history["trading_date"] == exit_date).sum()),
            },
            "diagnostic_reason_codes": reasons,
            "neighbouring_sessions": [{
                "trading_date": _iso(row.trading_date), "raw_close": float(row.close),
                "adjusted_close": float(row.adjusted_close), "volume": int(row.volume),
            } for row in neighbours],
        })
    return {
        "thresholds": {
            "near_zero_adjusted_close": policy.near_zero_adjusted_close,
            "extreme_absolute_return": policy.extreme_absolute_return,
        },
        "affected_total": int(len(affected)), "reported": len(output),
        "truncated": len(affected) > len(output), "affected_limit": affected_limit,
        "classification_counts": dict(sorted(classification_counts.items())),
        "region_counts": dict(sorted(region_counts.items())), "observations": output,
    }


def diagnose_extreme_labels(*, research_db: Path, production_db: Path,
                            decision_at: datetime | None = None,
                            policy: EvidencePolicy = EvidencePolicy(),
                            affected_limit: int = MAX_AFFECTED) -> dict[str, Any]:
    """Open only the research DB, verify both DBs, and fingerprint both twice."""
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    missing = [name for name, value in before.items() if not value.exists]
    if missing:
        raise ReadinessError(f"database path does not exist: {', '.join(missing)}")
    if _same_file(research_db, production_db):
        raise ReadinessError("research and production database paths are identical or aliased")
    captured = decision_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ReadinessError("decision_at must be timezone-aware")
    boundary = pd.Timestamp(captured).tz_convert("UTC").tz_localize(None).to_pydatetime()
    with duckdb.connect(str(research_db), read_only=True) as connection:
        _validate_schema(connection, boundary)
        retrieval = connection.execute("""SELECT retrieval_id FROM security_master_retrievals
            WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC,retrieval_id DESC LIMIT 1""",
            [boundary]).fetchone()
        if not retrieval:
            raise ReadinessError("no completed active catalogue exists at the decision boundary")
        catalogue = _frame(connection, """SELECT security_id,qualified_symbol,
            UPPER(primary_exchange) region,UPPER(currency) currency,
            (active AND instrument_type IN ('common_stock','ordinary_share')) eligible
            FROM security_listings WHERE retrieval_id=?""", [retrieval[0]])
        prices = _frame(connection, "SELECT * FROM global_price_observations")
        fx = _frame(connection, "SELECT * FROM global_fx_observations")
        actions = _frame(connection, "SELECT * FROM global_corporate_actions")
        failures = _frame(connection, """SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints
            WHERE status='failed' AND error_code IS NOT NULL""")
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx,
        actions=actions, failures=failures, decision_at=captured, policy=ObservationPolicy())
    predictions, evaluation = walk_forward_evidence(dataset.observations, prices,
        decision_at=captured, actions=actions,
        near_zero_adjusted_close=policy.near_zero_adjusted_close,
        extreme_absolute_return=policy.extreme_absolute_return,
        minimum_correlation_group_size=policy.minimum_correlation_group_size)
    visible_prices = prices.loc[pd.to_datetime(prices["retrieved_at"], utc=True).le(
        pd.Timestamp(captured).tz_convert("UTC")
    )]
    visible_actions = actions.loc[pd.to_datetime(actions["ex_date"]).le(
        pd.Timestamp(captured).tz_convert("UTC").tz_localize(None)
    )]
    diagnostic = diagnose_label_provenance(predictions, visible_prices, visible_actions, policy=policy,
                                            affected_limit=affected_limit)
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if before != after:
        raise ReadinessError("database fingerprint changed during read-only label diagnosis")
    return {
        "command": "diagnose-extreme-labels", "mode": "strictly_read_only",
        "decision_at": pd.Timestamp(captured).isoformat(),
        "panel": {"labels": int(len(predictions)), "vintages": evaluation.get("vintages", 0),
                  "construction": "research_scoring.walk_forward_evidence"},
        "diagnostic": diagnostic,
        "database_fingerprints": {key: {"before": asdict(before[key]),
            "after": asdict(after[key]), "unchanged": before[key] == after[key]} for key in before},
        "ranking": {"status": "withheld", "generated": False},
    }
