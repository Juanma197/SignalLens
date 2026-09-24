"""Controlled, point-in-time evaluation for the isolated Milestone 10 database.

Inputs are operator-owned files.  This module never opens the production database and
never writes ``prediction_vintages``.  Absence of adequate data is a valid result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd

RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_dataset_manifests
 (dataset_id VARCHAR PRIMARY KEY, manifest_json VARCHAR NOT NULL, content_hash VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
CREATE TABLE IF NOT EXISTS research_evaluation_runs
 (evaluation_id VARCHAR PRIMARY KEY, model_version VARCHAR NOT NULL, status VARCHAR NOT NULL,
  metrics_json VARCHAR NOT NULL, gates_json VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
CREATE TABLE IF NOT EXISTS research_current_shadow_vintages
 (vintage_id VARCHAR PRIMARY KEY, evaluation_id VARCHAR NOT NULL, evaluated_at TIMESTAMP NOT NULL,
  candidates_json VARCHAR NOT NULL, content_hash VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
"""

@dataclass(frozen=True)
class PromotionPolicy:
    minimum_months: int = 60
    minimum_securities: int = 100
    minimum_regions: int = 3
    minimum_securities_per_region: int = 20
    maximum_missingness: float = .20
    maximum_coefficient_drift: float = .25
    maximum_annual_turnover: float = 8.0
    maximum_drawdown: float = .35
    minimum_confidence: float = .55
    maximum_price_age_sessions: int = 2
    maximum_fx_age_sessions: int = 5
    maximum_fundamental_age_days: int = 550

def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()

def create_manifest(files: Iterable[Path], *, dataset_version: str, configuration: dict,
                    code_commit: str, retrieved_at: datetime, coverage: dict) -> dict:
    """Create a deterministic manifest. Volatile creation time is deliberately absent."""
    records = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}
               for p in sorted(map(Path, files), key=lambda item: item.name)]
    body = {"dataset_version": dataset_version, "sources": records,
            "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
            "coverage": coverage, "configuration": configuration, "code_commit": code_commit,
            "limitations": coverage.get("limitations", []), "synthetic_data": False}
    body["dataset_id"] = hashlib.sha256(canonical_json(body)).hexdigest()
    return body

def leakage_findings(frame: pd.DataFrame) -> list[dict]:
    """Reject any observation, retrieval, universe, feature, or execution after its cutoff."""
    required = {"security_id", "decision_at", "universe_available_at", "price_available_at",
                "feature_available_at", "execution_at"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing leakage columns: {sorted(missing)}")
    checked = frame.copy()
    for column in required - {"security_id"}:
        checked[column] = pd.to_datetime(checked[column], utc=True)
    findings = []
    for index, row in checked.iterrows():
        for column in ("universe_available_at", "price_available_at", "feature_available_at"):
            if row[column] > row.decision_at:
                findings.append({"row": int(index), "security_id": row.security_id,
                                 "field": column, "reason": "future_information"})
        if row.execution_at <= row.decision_at:
            findings.append({"row": int(index), "security_id": row.security_id,
                             "field": "execution_at", "reason": "not_next_session"})
    return findings

def trading_cost(region: str, side: str, notional: float, *, commission_bps: float = 5,
                 fx_bps: float = 3) -> float:
    """Explicit research assumption: UK buys add 50 bps stamp duty."""
    stamp = .005 if region.upper() == "UK" and side.lower() == "buy" else 0.0
    return notional * ((commission_bps + fx_bps) / 10_000 + stamp)

def bootstrap_mean_ci(values: Iterable[float], *, seed: int = 10, samples: int = 2000) -> list[float] | None:
    values = np.asarray(list(values), dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    return [float(x) for x in np.quantile(means, [.025, .975])]

def evaluate_gates(metrics: dict, policy: PromotionPolicy = PromotionPolicy()) -> dict:
    checks = {
        "monthly_vintages": metrics.get("monthly_vintages", 0) >= policy.minimum_months,
        "eligible_securities": metrics.get("eligible_securities", 0) >= policy.minimum_securities,
        "regional_coverage": len([v for v in metrics.get("securities_by_region", {}).values()
                                   if v >= policy.minimum_securities_per_region]) >= policy.minimum_regions,
        "missingness": metrics.get("missingness", 1) <= policy.maximum_missingness,
        "coefficient_stability": metrics.get("coefficient_drift", float("inf")) <= policy.maximum_coefficient_drift,
        "turnover": metrics.get("annual_turnover", float("inf")) <= policy.maximum_annual_turnover,
        "drawdown": abs(metrics.get("maximum_drawdown", -1)) <= policy.maximum_drawdown,
        "top_one_excess": metrics.get("top_one_excess_ci", [-1, -1])[0] > 0,
        "top_three_excess": metrics.get("top_three_excess_ci", [-1, -1])[0] > 0,
        "after_costs": metrics.get("after_cost_excess_return", -1) > 0,
        "robustness": all(metrics.get(k, False) for k in ("region_robust", "sector_robust", "regime_robust")),
        "no_leakage": metrics.get("leakage_findings", 1) == 0,
        "not_concentrated": metrics.get("largest_security_contribution", 1) <= .25,
        "untouched_test": bool(metrics.get("untouched_forward_test")),
        "historical_membership": bool(metrics.get("historical_membership_complete")),
        "delistings": bool(metrics.get("delistings_included")),
        "current_universe_coverage": bool(metrics.get("current_universe_coverage_pass")),
        "prices_fresh": bool(metrics.get("prices_fresh")),
        "fx_fresh": bool(metrics.get("fx_fresh")),
        "fundamentals_fresh": bool(metrics.get("fundamentals_fresh")),
        "model_evaluation_complete": bool(metrics.get("model_evaluation_complete")),
        "hard_risk_gates": bool(metrics.get("hard_risk_gates_passed")),
    }
    return {"policy": asdict(policy), "checks": checks, "passed": all(checks.values()),
            "failed": sorted(k for k, passed in checks.items() if not passed)}

class ResearchEvaluationRepository:
    def __init__(self, research_path: Path | str, *, production_path: Path | str | None = None):
        self.path = Path(research_path)
        if production_path and self.path.resolve() == Path(production_path).resolve():
            raise ValueError("Research database must be separate from production")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as db:
            db.execute(RESEARCH_SCHEMA)

    def store_manifest(self, manifest: dict) -> dict:
        self.initialize(); payload = canonical_json(manifest); digest = hashlib.sha256(payload).hexdigest()
        with duckdb.connect(str(self.path)) as db:
            old = db.execute("SELECT content_hash FROM research_dataset_manifests WHERE dataset_id=?",
                             [manifest["dataset_id"]]).fetchone()
            if old and old[0] != digest: raise ValueError("Manifest is immutable")
            db.execute("INSERT OR IGNORE INTO research_dataset_manifests VALUES (?,?,?,?)",
                       [manifest["dataset_id"], payload.decode(), digest, datetime.now(timezone.utc).replace(tzinfo=None)])
        return {"status": "already_exists" if old else "completed", "dataset_id": manifest["dataset_id"]}

    def store_evaluation(self, evaluation_id: str, model_version: str, metrics: dict) -> dict:
        gates = evaluate_gates(metrics); self.initialize()
        with duckdb.connect(str(self.path)) as db:
            db.execute("INSERT OR IGNORE INTO research_evaluation_runs VALUES (?,?,?,?,?,?)",
                       [evaluation_id, model_version, "passed" if gates["passed"] else "research_only",
                        canonical_json(metrics).decode(), canonical_json(gates).decode(), datetime.now(timezone.utc).replace(tzinfo=None)])
        return gates

    def create_shadow(self, evaluation_id: str, evaluated_at: datetime, candidates: list[dict]) -> dict:
        self.initialize()
        with duckdb.connect(str(self.path), read_only=True) as db:
            row = db.execute("SELECT gates_json FROM research_evaluation_runs WHERE evaluation_id=?", [evaluation_id]).fetchone()
        if not row or not json.loads(row[0])["passed"]:
            return {"status": "withheld", "reason": "promotion_gates_failed", "candidates": 0}
        qualified = [c for c in candidates if c.get("eligible") and not c.get("hard_risk", False)
                     and c.get("confidence", 0) >= .55
                     and c.get("price_age_sessions", 10**6) <= 2
                     and c.get("fx_age_sessions", 10**6) <= 5
                     and c.get("fundamental_age_days", 10**6) <= 550][:3]
        payload = canonical_json(qualified); digest = hashlib.sha256(payload).hexdigest()
        vintage_id = hashlib.sha256(canonical_json([evaluation_id, evaluated_at.isoformat(), digest])).hexdigest()
        with duckdb.connect(str(self.path)) as db:
            old = db.execute("SELECT content_hash FROM research_current_shadow_vintages WHERE vintage_id=?", [vintage_id]).fetchone()
            if old and old[0] != digest: raise ValueError("Shadow vintage is immutable")
            db.execute("INSERT OR IGNORE INTO research_current_shadow_vintages VALUES (?,?,?,?,?,?)",
                       [vintage_id, evaluation_id, evaluated_at.astimezone(timezone.utc).replace(tzinfo=None),
                        payload.decode(), digest, datetime.now(timezone.utc).replace(tzinfo=None)])
        return {"status": "already_exists" if old else "completed", "vintage_id": vintage_id,
                "candidates": len(qualified), "label": "RESEARCH ONLY — NOT INVESTMENT ADVICE"}

    def status(self) -> dict:
        if not self.path.exists(): return {"status": "unavailable", "label": "RESEARCH ONLY", "evaluations": 0}
        with duckdb.connect(str(self.path), read_only=True) as db:
            tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
            count = lambda table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table in tables else 0
            latest = db.execute("SELECT model_version,status,gates_json,metrics_json FROM research_evaluation_runs ORDER BY created_at DESC LIMIT 1").fetchone() if "research_evaluation_runs" in tables else None
        return {"status": "available" if latest else "unavailable", "label": "RESEARCH ONLY — NOT INVESTMENT ADVICE",
                "evaluations": count("research_evaluation_runs"), "shadow_vintages": count("research_current_shadow_vintages"),
                "latest": None if not latest else {"model_version": latest[0], "evaluation_status": latest[1],
                    "gates": json.loads(latest[2]), "metrics": json.loads(latest[3])}}
