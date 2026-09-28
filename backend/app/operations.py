"""Fail-closed, research-only operator facade used by the web API."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .eodhd_ingestion import EODHDClient, EODHDIngestion, EODHDLimits
from .model_readiness import _same_file, assess_model_readiness, fingerprint
from .research_scoring import assess_research_scoring
from .shadow_portfolios import plan_shadow_vintage, shadow_status

LABEL = "RESEARCH ONLY — NOT INVESTMENT ADVICE"


def safe_error(code: str = "operation_failed") -> dict[str, str]:
    """Return a stable public error; never serialize provider or filesystem details."""
    return {"code": code, "message": "Research operation failed; details were redacted."}


def research_health(research_db: Path, production_db: Path) -> dict[str, Any]:
    research, production = fingerprint(research_db), fingerprint(production_db)
    return {"label": LABEL, "research_database_available": research.exists,
            "database_isolation_confirmed": not _same_file(research_db, production_db),
            "production_publishing_available": False}


def build_ingestion(research_db: Path, production_db: Path, token: str,
                    *, transport: Callable[..., Any] | None = None) -> EODHDIngestion:
    return EODHDIngestion(research_db, production_db,
                          EODHDClient(token, EODHDLimits(), transport=transport))


def dashboard_status(research_db: Path, production_db: Path,
                     decision_at: datetime | None = None) -> dict[str, Any]:
    """Compose read-only domain reports without opening either database for writing."""
    captured = decision_at or datetime.now(timezone.utc)
    result: dict[str, Any] = {**research_health(research_db, production_db),
                              "captured_at": captured.isoformat()}
    try:
        readiness = assess_model_readiness(research_db=research_db,
            production_db=production_db, decision_at=captured, sample_limit=10)
        scoring = assess_research_scoring(research_db=research_db,
            production_db=production_db, decision_at=captured)
        shadow = shadow_status(research_db=research_db, production_db=production_db)
        catalogue = readiness.get("active_catalogue", {})
        result["summary"] = {
            "latest_catalogue_retrieval": catalogue.get("retrieved_at"),
            "price_freshness": readiness.get("price_quality"),
            "fx_freshness": readiness.get("fx_quality"),
            "selected_count": readiness.get("selected_securities", 0),
            "model_ready_count": readiness.get("model_ready_securities", 0),
            "withheld_count": readiness.get("withheld_securities", 0),
            "permanently_failed_count": readiness.get("provider_failures", {}).get("permanent", 0),
            "latest_ingestion_result": catalogue.get("status"),
            "shadow_vintage_count": len(shadow.get("vintages", [])),
            "shadow_cohorts": shadow.get("cohorts", []),
            "strategy_version": (shadow.get("vintages") or [{}])[-1].get("strategy_version"),
            "configuration_hash": (shadow.get("vintages") or [{}])[-1].get("configuration_hash"),
            "evidence_gates": scoring.get("evidence_gates"),
            "maturity_horizons": [126, 252],
        }
        result["status"] = "available"
    except Exception:
        result.update(status="unavailable", error=safe_error("readiness_unavailable"))
    return result
