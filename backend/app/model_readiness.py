"""Read-only bridge from the research DuckDB to model-ready observations.

This module deliberately owns no schema and performs no initialization.  It
loads the existing research tables through a read-only DuckDB connection and
delegates all observation eligibility decisions to ``research_observations``.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .eodhd_ingestion import PERMANENT_FAILURE_CODES, RETRYABLE_FAILURE_CODES
from .research_observations import (
    EXPECTED_REGIONS,
    ObservationPolicy,
    ObservationValidationError,
    build_model_ready_observations,
)

SAMPLE_LIMIT = 10
REQUIRED_COLUMNS = {
    "security_master_retrievals": {"retrieval_id", "retrieved_at", "status"},
    "security_listings": {
        "retrieval_id", "security_id", "qualified_symbol", "primary_exchange",
        "currency", "instrument_type", "active",
    },
    "global_price_observations": {
        "qualified_symbol", "trading_date", "currency", "open", "high", "low",
        "close", "adjusted_close", "volume", "status", "source", "retrieved_at",
    },
    "global_fx_observations": {
        "base_currency", "quote_currency", "observed_on", "rate", "available_at",
    },
    "global_corporate_actions": {"qualified_symbol", "ex_date", "action_type", "value"},
    "eodhd_ingestion_checkpoints": {
        "stage", "qualified_symbol", "status", "error_code", "updated_at",
    },
    "eodhd_catalogue_validations": {"validated_at", "status", "accepted", "zero_regions_json"},
}


class ReadinessError(ValueError):
    """A path, schema, or database-integrity precondition was not established."""


@dataclass(frozen=True)
class FileFingerprint:
    exists: bool
    bytes: int | None
    sha256: str | None


def fingerprint(path: Path) -> FileFingerprint:
    if not path.exists():
        return FileFingerprint(False, None, None)
    if not path.is_file():
        raise ReadinessError(f"database path is not a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return FileFingerprint(True, size, digest.hexdigest())


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _frame(connection: duckdb.DuckDBPyConnection, query: str, parameters: list[Any] | None = None) -> pd.DataFrame:
    return connection.execute(query, parameters or []).fetchdf()


def _validate_schema(connection: duckdb.DuckDBPyConnection, boundary: datetime) -> None:
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    missing_tables = set(REQUIRED_COLUMNS) - tables
    if missing_tables:
        raise ReadinessError(f"incomplete research schema; missing tables: {', '.join(sorted(missing_tables))}")
    for table, expected in REQUIRED_COLUMNS.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()}
        missing = expected - actual
        if missing:
            raise ReadinessError(
                f"incompatible research schema; {table} missing columns: {', '.join(sorted(missing))}"
            )
    validation = connection.execute(
        """SELECT status FROM eodhd_catalogue_validations WHERE validated_at<=?
           ORDER BY validated_at DESC LIMIT 1""", [boundary]
    ).fetchone()
    if validation is None or validation[0] != "validated":
        raise ReadinessError("latest catalogue validation is absent or unsuccessful")
    # Force DuckDB to read catalogue metadata before any assessment is trusted.
    connection.execute("SELECT COUNT(*) FROM duckdb_tables()").fetchone()


def _counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty:
        return {}
    return {str(key): int(value) for key, value in frame[column].value_counts().sort_index().items()}


def assess_model_readiness(
    *,
    research_db: Path,
    production_db: Path,
    decision_at: datetime | None = None,
    policy: ObservationPolicy = ObservationPolicy(),
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Assess an existing research database without writing any database file."""
    if sample_limit < 0 or sample_limit > 25:
        raise ReadinessError("sample_limit must be between 0 and 25")
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if not before["research"].exists:
        raise ReadinessError("research database does not exist; refusing to create it")
    if _same_file(research_db, production_db):
        raise ReadinessError("research and production database paths are identical or aliased")
    captured = decision_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ReadinessError("decision_at must be timezone-aware")
    boundary = captured.astimezone(timezone.utc).replace(tzinfo=None)

    try:
        with duckdb.connect(str(research_db), read_only=True) as connection:
            _validate_schema(connection, boundary)
            retrieval = connection.execute(
                """SELECT retrieval_id, retrieved_at FROM security_master_retrievals
                   WHERE status='completed' AND retrieved_at<=?
                   ORDER BY retrieved_at DESC, retrieval_id DESC LIMIT 1""", [boundary]
            ).fetchone()
            if retrieval is None:
                raise ReadinessError("no completed active catalogue exists at the decision boundary")
            catalogue = _frame(connection, """SELECT security_id, qualified_symbol,
                    UPPER(primary_exchange) AS region, UPPER(currency) AS currency,
                    (active AND instrument_type IN ('common_stock','ordinary_share')) AS eligible
                FROM security_listings WHERE retrieval_id=? ORDER BY qualified_symbol""", [retrieval[0]])
            prices = _frame(connection, """SELECT qualified_symbol,trading_date,currency,open,high,low,
                    close,adjusted_close,volume,status,source,retrieved_at
                FROM global_price_observations ORDER BY qualified_symbol,trading_date,source""")
            fx = _frame(connection, """SELECT base_currency,quote_currency,observed_on,rate,available_at
                FROM global_fx_observations ORDER BY base_currency,quote_currency,observed_on""")
            actions = _frame(connection, """SELECT qualified_symbol,ex_date,action_type,value
                FROM global_corporate_actions ORDER BY qualified_symbol,ex_date,action_type""")
            failures = _frame(connection, """SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints
                WHERE status='failed' AND error_code IS NOT NULL ORDER BY qualified_symbol,error_code""")
    except duckdb.Error as exc:
        raise ReadinessError("research database/schema integrity could not be established") from exc

    try:
        dataset = build_model_ready_observations(
            catalogue=catalogue, prices=prices, fx=fx, actions=actions, failures=failures,
            decision_at=captured, policy=policy,
        )
    except ObservationValidationError:
        raise

    observations = dataset.observations
    selected_symbols = set(catalogue.loc[catalogue["eligible"].astype(bool), "qualified_symbol"])
    visible_prices = prices.loc[
        prices["qualified_symbol"].isin(selected_symbols)
        & pd.to_datetime(prices["retrieved_at"], utc=True).le(pd.Timestamp(captured))
        & prices["status"].eq("available")
    ]
    loaded_symbols = set(visible_prices["qualified_symbol"])
    affected = observations.loc[~observations["eligible"]].sort_values("qualified_symbol")
    failure_codes = failures["error_code"].astype(str).str.lower()
    permanent = failures.loc[failure_codes.isin(PERMANENT_FAILURE_CODES)]
    retryable = failures.loc[failure_codes.isin(RETRYABLE_FAILURE_CODES)]
    action_invalid = observations["exclusion_reasons"].map(lambda values: "invalid_corporate_action" in values)
    duplicate_counts = {
        "prices": int(prices.duplicated(["qualified_symbol", "trading_date"]).sum()),
        "fx": int(fx.duplicated(["base_currency", "quote_currency", "observed_on"]).sum()),
        "corporate_actions": int(actions.duplicated(["qualified_symbol", "ex_date", "action_type"]).sum()),
        "provider_failures": int(failures.duplicated(["qualified_symbol", "error_code"]).sum()),
    }
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    files = {
        name: {"before": asdict(before[name]), "after": asdict(after[name]),
               "unchanged": before[name] == after[name]}
        for name in ("research", "production")
    }
    if not all(item["unchanged"] for item in files.values()):
        raise ReadinessError("database fingerprint changed during read-only assessment")

    report = {
        **dataset.report,
        "command": "model-readiness",
        "mode": "strictly_read_only",
        "status": "ready" if bool(observations["eligible"].all()) else "withheld",
        "research_only": True,
        "not_investment_advice": True,
        "survivorship_free": False,
        "ranking_generated": False,
        "top_3_generated": False,
        "highest_conviction_candidate_generated": False,
        "selected_securities": int(len(selected_symbols)),
        "loaded_securities": int(len(loaded_symbols)),
        "model_ready_securities": int(observations["eligible"].sum()),
        "withheld_securities": int((~observations["eligible"]).sum()),
        "selected_by_region": _counts(catalogue.loc[catalogue["eligible"]], "region"),
        "selected_by_currency": _counts(catalogue.loc[catalogue["eligible"]], "currency"),
        "loaded_by_region": _counts(observations.loc[observations["qualified_symbol"].isin(loaded_symbols)], "region"),
        "loaded_by_currency": _counts(observations.loc[observations["qualified_symbol"].isin(loaded_symbols)], "currency"),
        "model_ready_by_region": _counts(observations.loc[observations["eligible"]], "region"),
        "model_ready_by_currency": _counts(observations.loc[observations["eligible"]], "currency"),
        "withheld_by_region": _counts(observations.loc[~observations["eligible"]], "region"),
        "withheld_by_currency": _counts(observations.loc[~observations["eligible"]], "currency"),
        "price_quality": {
            "minimum_sessions_required": policy.minimum_price_sessions,
            "freshness_limit_days": policy.maximum_price_age_days,
            "history_depth": {
                "minimum": int(observations["price_sessions"].min()),
                "maximum": int(observations["price_sessions"].max()),
            },
            "stale": int(observations["exclusion_reasons"].map(lambda x: "stale_price" in x).sum()),
            "short": int(observations["exclusion_reasons"].map(lambda x: "insufficient_history" in x).sum()),
            "invalid": int(observations["exclusion_reasons"].map(lambda x: "invalid_price_history" in x).sum()),
        },
        "fx_quality": {
            "required_currencies": ["CAD", "EUR", "USD"],
            "currencies_available": sorted(set(fx.loc[fx["quote_currency"] == "GBP", "base_currency"])),
            "point_in_time_semantics": "latest_available_on_or_before_price_date",
            "freshness_limit_days": policy.maximum_fx_age_days,
            "missing": int(observations["exclusion_reasons"].map(lambda x: "missing_fx" in x).sum()),
            "stale": int(observations["exclusion_reasons"].map(lambda x: "stale_fx" in x).sum()),
            "invalid": int(observations["exclusion_reasons"].map(lambda x: "invalid_fx" in x).sum()),
            "gbp_identity_conversion": True,
            "gbx_divisor": 100,
        },
        "provider_failures": {
            "permanent": int(len(permanent)), "retryable": int(len(retryable)),
            "other": int(len(failures) - len(permanent) - len(retryable)),
        },
        "observation_integrity": {"duplicate_natural_keys": duplicate_counts},
        "corporate_actions": {
            "observations": int(len(actions)), "invalid_securities": int(action_invalid.sum()),
            "status": "valid" if not action_invalid.any() else "invalid_observations_withheld",
        },
        "affected_symbols_sample": [
            {"symbol": row.qualified_symbol, "reasons": row.exclusion_reasons}
            for row in affected.head(sample_limit).itertuples()
        ],
        "affected_symbols_sample_limit": sample_limit,
        "affected_symbols_sample_truncated": len(affected) > sample_limit,
        "database_fingerprints": files,
        "active_catalogue": {"retrieval_id": retrieval[0], "retrieved_at": retrieval[1]},
        "required_regions": sorted(EXPECTED_REGIONS),
    }
    return report
