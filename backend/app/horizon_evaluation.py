"""Milestone 18: pre-registered, leakage-safe multi-horizon evidence.

This module is intentionally incapable of producing candidates.  The horizon set,
dependence treatment, correction and gates are constants so a run cannot tune them
after inspecting its results.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, _frame, _same_file, _validate_schema, fingerprint
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import EvidencePolicy, evaluate_evidence, walk_forward_evidence

LOCKED_HORIZONS = (21, 63, 126, 252)
CORRECTION_METHOD = "holm_bonferroni_familywise_alpha_0.05"
FAMILY_ALPHA = .05
FROZEN_21_SESSION_BASELINE = {
    "predictions": 41271, "vintages": 113, "mean_excess_return": -.00463,
    "positive_period_rate": .4779, "rank_correlation": .04284,
    "outcome": "failed_ranking_withheld",
}


def horizon_block_inference(values: list[float] | np.ndarray, horizon: int, *,
                            samples: int = 4000, seed: int = 18) -> dict[str, Any]:
    """Circular moving-block bootstrap over monthly vintage results.

    Block length is ceil(horizon / 21), retaining the serial dependence induced
    by overlapping labels.  The p-value is the one-sided null-centred bootstrap
    probability for a non-positive equal-vintage mean.
    """
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    block = ceil(horizon / 21)
    if len(data) < max(2, block):
        return {"method": "circular_moving_block_bootstrap", "block_vintages": block,
                "samples": samples, "confidence_interval": None, "raw_p_value": None}
    rng = np.random.default_rng(seed + horizon)
    starts = rng.integers(0, len(data), size=(samples, ceil(len(data) / block)))
    offsets = np.arange(block)
    indexes = (starts[..., None] + offsets) % len(data)
    boot = data[indexes].reshape(samples, -1)[:, :len(data)].mean(axis=1)
    centred = data - data.mean()
    null_boot = centred[indexes].reshape(samples, -1)[:, :len(data)].mean(axis=1)
    p_value = (1 + int(np.count_nonzero(null_boot >= data.mean()))) / (samples + 1)
    return {"method": "circular_moving_block_bootstrap", "block_vintages": block,
            "samples": samples, "confidence_interval": [float(x) for x in np.quantile(boot, [.025, .975])],
            "raw_p_value": float(p_value)}


def holm_bonferroni(p_values: dict[int, float | None], alpha: float = FAMILY_ALPHA) -> dict[int, dict[str, Any]]:
    """Return Holm adjusted p-values and decisions for the complete locked family."""
    if set(p_values) != set(LOCKED_HORIZONS):
        raise ValueError("multiple-testing family must contain exactly the four locked horizons")
    finite = [(h, float(p)) for h, p in p_values.items() if p is not None and np.isfinite(p)]
    ordered = sorted(finite, key=lambda item: (item[1], item[0]))
    adjusted: dict[int, float] = {}
    running = 0.0
    m = len(LOCKED_HORIZONS)
    for rank, (horizon, value) in enumerate(ordered):
        running = max(running, min(1.0, (m - rank) * value))
        adjusted[horizon] = running
    return {h: {"raw_p_value": p_values[h], "adjusted_p_value": adjusted.get(h),
                "passed": h in adjusted and adjusted[h] <= alpha}
            for h in LOCKED_HORIZONS}


def evaluate_horizon_frames(observations: pd.DataFrame, prices: pd.DataFrame, *,
                            decision_at: datetime, actions: pd.DataFrame | None = None,
                            policy: EvidencePolicy = EvidencePolicy()) -> dict[str, Any]:
    results: dict[int, dict[str, Any]] = {}
    for horizon in LOCKED_HORIZONS:
        predictions, metrics = walk_forward_evidence(
            observations, prices, decision_at=decision_at, horizon_sessions=horizon,
            actions=actions, near_zero_adjusted_close=policy.near_zero_adjusted_close,
            extreme_absolute_return=policy.extreme_absolute_return,
            minimum_correlation_group_size=policy.minimum_correlation_group_size)
        if predictions.empty:
            excess = []
        else:
            top = predictions.loc[predictions.groupby("vintage_date")["score"].rank(
                method="first", ascending=False).le(3)].groupby("vintage_date")["forward_return"].mean()
            excess = (top - predictions.groupby("vintage_date")["forward_return"].mean()).tolist()
        dependence = horizon_block_inference(excess, horizon)
        gates = evaluate_evidence(observations=observations, evaluation=metrics,
                                  integrity_ok=True, policy=policy)
        results[horizon] = {"horizon_sessions": horizon, "evaluation": metrics,
                            "dependence_adjusted_inference": dependence, "evidence_gates": gates}
    correction = holm_bonferroni({h: r["dependence_adjusted_inference"]["raw_p_value"]
                                  for h, r in results.items()})
    for horizon, result in results.items():
        result["multiple_testing"] = correction[horizon]
        result["horizon_gates"] = {**result["evidence_gates"]["checks"],
            "confidence": bool(result["dependence_adjusted_inference"]["confidence_interval"]
                               and result["dependence_adjusted_inference"]["confidence_interval"][0] > 0),
            "multiple_testing_adjustment": correction[horizon]["passed"]}
        result["passed"] = all(result["horizon_gates"].values())
        result["candidate_generation_authorized"] = False
    return {"locked_horizons": list(LOCKED_HORIZONS), "horizons_locked_before_evaluation": True,
            "multiple_testing_method": CORRECTION_METHOD, "family_alpha": FAMILY_ALPHA,
            "dependence_treatment": "circular moving-block bootstrap of ordered monthly vintage excess returns; block=ceil(horizon/21)",
            "results": {str(k): v for k, v in results.items()}}


def assess_horizon_evaluation(*, research_db: Path, production_db: Path,
                              decision_at: datetime | None = None,
                              policy: EvidencePolicy = EvidencePolicy()) -> dict[str, Any]:
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if not before["research"].exists:
        raise ReadinessError("research database does not exist; refusing to create it")
    if _same_file(research_db, production_db):
        raise ReadinessError("research and production database paths are identical or aliased")
    captured = decision_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ReadinessError("decision_at must be timezone-aware")
    boundary = pd.Timestamp(captured).tz_convert("UTC").tz_localize(None).to_pydatetime()
    with duckdb.connect(str(research_db), read_only=True) as connection:
        _validate_schema(connection, boundary)
        retrieval = connection.execute("""SELECT retrieval_id FROM security_master_retrievals
            WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC,retrieval_id DESC LIMIT 1""", [boundary]).fetchone()
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
    evaluation = evaluate_horizon_frames(dataset.observations, prices, decision_at=captured,
                                         actions=actions, policy=policy)
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if any(before[k] != after[k] for k in before):
        raise ReadinessError("database fingerprint changed during read-only horizon evaluation")
    return {"command": "research-horizon-evaluation", "mode": "strictly_read_only",
            "decision_at": pd.Timestamp(captured).isoformat(), "research_only": True,
            "horizon_selection_evidence_only": True, "candidates": [], "ranking_generated": False,
            "frozen_21_session_baseline": FROZEN_21_SESSION_BASELINE,
            "policy": asdict(policy), **evaluation,
            "database_fingerprints": {k: {"before": asdict(before[k]), "after": asdict(after[k]),
                "unchanged": before[k] == after[k]} for k in before}}
