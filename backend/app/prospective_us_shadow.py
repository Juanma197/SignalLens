"""Milestone 28: locked, prospective-only US dilution-overlay paper research."""
from __future__ import annotations

import hashlib
import json
import base64
import hmac
import calendar
from dataclasses import asdict
from datetime import date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, _same_file, fingerprint
from .shadow_portfolios import maturity_session
from .active_catalogue import select_active_catalogue
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import prepare_cross_section
from .price_segments import detect_price_segments

SPEC_PATH = Path(__file__).with_name("prospective_us_shadow_v1.json")
AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY PROSPECTIVE SHADOW CREATION"
LABEL = "PAPER RESEARCH SELECTIONS — NOT INVESTMENT ADVICE"
PLAN_TTL = timedelta(minutes=10)
MAX_OUTPUT_ROWS = 25
PLAN_TOKEN_VERSION = 1


def canonical_specification() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def configuration_hash(specification: dict[str, Any] | None = None) -> str:
    payload = json.dumps(specification or canonical_specification(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


SPECIFICATION = canonical_specification()
CONFIGURATION_HASH = configuration_hash()
STRATEGY_VERSION = SPECIFICATION["semantic_version"]
REGISTRATION_AT = datetime.fromisoformat(SPECIFICATION["registration_timestamp"].replace("Z", "+00:00"))


def _paths(research: Path, production: Path) -> tuple[Any, Any]:
    if not research.is_file() or not production.is_file():
        raise ReadinessError("explicit existing research and production database paths are required")
    if research.is_symlink() or production.is_symlink() or _same_file(research, production):
        raise ReadinessError("database paths must be distinct, non-aliased regular files")
    return fingerprint(research), fingerprint(production)


def score_inputs(prices: pd.DataFrame, dilution: pd.DataFrame, *, decision_at: datetime,
                 maximum_age_days: int = 550) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create the locked 90/10 score from information known at decision time."""
    if decision_at.tzinfo is None:
        raise ReadinessError("decision timestamp must be timezone-aware")
    required_price = {"security_id", "qualified_symbol", "price_percentile", "decision_price",
                      "model_ready", "price_date"}
    required_dilution = {"security_id", "diluted_share_growth", "period_end", "filed_at",
                         "retrieved_at", "reliable", "compatible", "provenance"}
    if required_price - set(prices) or required_dilution - set(dilution):
        raise ReadinessError("prospective score input schema is incomplete")
    merged = prices.merge(dilution, on="security_id", how="left", suffixes=("", "_dilution"))
    cutoff = pd.Timestamp(decision_at)
    for column in ("filed_at", "retrieved_at"):
        merged[column] = pd.to_datetime(merged[column], utc=True, errors="coerce")
    merged["period_end"] = pd.to_datetime(merged.period_end, errors="coerce")
    growth = pd.to_numeric(merged.diluted_share_growth, errors="coerce")
    age = (cutoff.tz_localize(None) - merged.period_end).dt.days
    merged["dilution_available"] = (growth.notna() & np.isfinite(growth) & merged.reliable.eq(True)
        & merged.compatible.eq(True) & merged.filed_at.le(cutoff) & merged.retrieved_at.le(cutoff)
        & age.between(0, maximum_age_days))
    merged["eligible"] = merged.model_ready.eq(True) & merged.dilution_available
    reasons = []
    for row in merged.itertuples():
        row_reasons = []
        if not bool(row.model_ready): row_reasons.append("price_not_model_ready")
        if not bool(row.dilution_available): row_reasons.append("dilution_unavailable")
        reasons.append(row_reasons)
    merged["withholding_reasons"] = reasons
    eligible = merged.loc[merged.eligible].copy()
    # Lower share growth (including buybacks) is better. Stable symbol is the rank tie breaker.
    eligible["dilution_percentile"] = eligible.diluted_share_growth.rank(
        method="average", pct=True, ascending=False)
    eligible["prospective_score"] = .9 * pd.to_numeric(eligible.price_percentile) + .1 * eligible.dilution_percentile
    eligible = eligible.sort_values(["prospective_score", "qualified_symbol"], ascending=[False, True])
    selected = eligible.head(3).copy()
    return eligible, selected


def _plan_payload(decision_at: datetime, eligible: pd.DataFrame, selected: pd.DataFrame,
                  generated_at: datetime, readiness: dict[str, Any]) -> dict[str, Any]:
    payload = {"strategy_version": STRATEGY_VERSION, "configuration_hash": CONFIGURATION_HASH,
        "decision_at": pd.Timestamp(decision_at).isoformat(), "vintage_month": decision_at.strftime("%Y-%m"),
        "generated_at": pd.Timestamp(generated_at).isoformat(), "eligible_ids": eligible.security_id.astype(str).tolist(),
        "selected_ids": selected.security_id.astype(str).tolist(), "readiness": readiness}
    payload["plan_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def validate_prospective_boundary(decision_at: datetime) -> None:
    if decision_at.strftime("%Y-%m") <= "2026-09":
        raise ReadinessError("September 2026 and earlier vintages cannot be reconstructed")
    if decision_at.tzinfo is None or decision_at <= REGISTRATION_AT:
        raise ReadinessError("first decision vintage must be strictly after registration")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReadinessError("decision timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _token(payload: dict[str, Any], research_fingerprint: Any,
           production_fingerprint: Any) -> str:
    """Create a self-contained, expiring integrity token (not an authorization)."""
    body = json.dumps({"v": PLAN_TOKEN_VERSION, "plan": payload}, sort_keys=True,
                      separators=(",", ":"), default=str).encode()
    key_material = json.dumps({"configuration_hash": CONFIGURATION_HASH,
        "research": asdict(research_fingerprint), "production": asdict(production_fingerprint)},
        sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(hashlib.sha256(key_material).digest(), body, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + signature


def _decode_token(value: str, research_fingerprint: Any,
                  production_fingerprint: Any) -> dict[str, Any]:
    try:
        encoded, supplied = value.split(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        envelope = json.loads(body)
        expected = _token(envelope["plan"], research_fingerprint,
                          production_fingerprint).rsplit(".", 1)[1]
        if envelope.get("v") != PLAN_TOKEN_VERSION or not hmac.compare_digest(supplied, expected):
            raise ValueError
        return envelope["plan"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ReadinessError("plan identifier is invalid, modified, or for different databases") from exc


def _table_names(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}


def _require_tables(db: duckdb.DuckDBPyConnection, required: set[str]) -> None:
    missing = required - _table_names(db)
    if missing:
        raise ReadinessError(f"research database is missing required tables: {', '.join(sorted(missing))}")


def _dilution_from_facts(facts: pd.DataFrame, securities: pd.DataFrame,
                         decision_at: datetime) -> pd.DataFrame:
    """Choose the latest two defensible annual diluted-share facts known at cutoff."""
    columns = ["security_id", "diluted_share_growth", "period_end", "filed_at",
               "retrieved_at", "reliable", "compatible", "provenance"]
    if facts.empty:
        return pd.DataFrame(columns=columns)
    frame = facts.copy()
    frame["public_at"] = pd.to_datetime(frame.public_at, utc=True, errors="coerce")
    frame["retrieved_at"] = pd.to_datetime(frame.retrieved_at, utc=True, errors="coerce")
    frame["period_end"] = pd.to_datetime(frame.period_end, errors="coerce")
    frame = frame.loc[
        frame.concept.eq("WeightedAverageNumberOfDilutedSharesOutstanding")
        & frame.form.isin(["10-K", "10-K/A", "20-F", "20-F/A"])
        & frame.public_at.le(pd.Timestamp(decision_at))
        & frame.retrieved_at.le(pd.Timestamp(decision_at))
        & pd.to_numeric(frame.value, errors="coerce").gt(0)
    ].copy()
    # A later public amendment supersedes the original only after it became public.
    frame = frame.sort_values(["security_id", "period_end", "public_at", "retrieved_at",
                               "accession_number"]).drop_duplicates(
        ["security_id", "period_end"], keep="last")
    rows: list[dict[str, Any]] = []
    symbols = securities.set_index("security_id").qualified_symbol.to_dict()
    for security_id, group in frame.groupby("security_id", sort=True):
        annual = group.sort_values("period_end").tail(2)
        if len(annual) != 2:
            continue
        prior, current = annual.iloc[0], annual.iloc[1]
        compatible = str(prior.unit).lower() == str(current.unit).lower() == "shares"
        gap = (current.period_end - prior.period_end).days
        growth = ((float(current.value) - float(prior.value)) / abs(float(prior.value))
                  if compatible and 300 <= gap <= 430 else np.nan)
        rows.append({"security_id": str(security_id), "qualified_symbol": symbols.get(security_id),
            "diluted_share_growth": growth, "period_end": current.period_end,
            "filed_at": current.public_at, "retrieved_at": current.retrieved_at,
            "reliable": bool(np.isfinite(growth)), "compatible": compatible,
            "provenance": f"sec:{current.accession_number}"})
    return pd.DataFrame(rows, columns=columns + ["qualified_symbol"])


def database_inputs(*, research_db: Path, production_db: Path, decision_at: datetime,
                    session_date: date, require_fx: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Construct authoritative point-in-time inputs without opening either DB writable."""
    decision_at = _utc(decision_at)
    before = _paths(research_db, production_db)
    with duckdb.connect(str(production_db), read_only=True) as production:
        production.execute("SELECT 1")
    required = {"security_master_retrievals", "security_listings", "global_price_observations",
        "global_fx_observations", "global_exchange_sessions", "global_corporate_actions",
        "eodhd_ingestion_checkpoints", "sec_issuers", "sec_checkpoints", "sec_facts"}
    with duckdb.connect(str(research_db), read_only=True) as db:
        _require_tables(db, required)
        boundary = decision_at.replace(tzinfo=None)
        active = select_active_catalogue(db, as_of=boundary)
        if active is None:
            raise ReadinessError("no completed active catalogue exists at the decision boundary")
        catalogue = active.listings
        us = catalogue.loc[catalogue.region.eq("US") & catalogue.eligible].copy()
        sessions = db.execute("""SELECT session_date,is_open,retrieved_at FROM global_exchange_sessions
            WHERE UPPER(exchange) IN ('US','NYSE','NASDAQ') AND session_date>=? AND session_date<=?
            ORDER BY session_date""", [session_date.replace(day=1), date(session_date.year, session_date.month,
            calendar.monthrange(session_date.year, session_date.month)[1])]).fetchdf()
        prices = db.execute("SELECT * FROM global_price_observations").fetchdf()
        fx = db.execute("SELECT * FROM global_fx_observations").fetchdf()
        actions = db.execute("SELECT * FROM global_corporate_actions").fetchdf()
        failures = db.execute("SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints WHERE status='failed'").fetchdf()
        checkpoints = db.execute("SELECT security_id,status,updated_at FROM sec_checkpoints").fetchdf()
        issuers = db.execute("SELECT security_id,mapped_at FROM sec_issuers").fetchdf()
        facts = db.execute("SELECT * FROM sec_facts").fetchdf()
    open_dates = pd.to_datetime(sessions.loc[sessions.is_open.eq(True), "session_date"]).dt.date.tolist()
    session_known = (session_date in open_dates and bool(open_dates) and session_date == max(open_dates)
                     and pd.to_datetime(sessions.retrieved_at, utc=True).le(pd.Timestamp(decision_at)).all())
    if not session_known:
        raise ReadinessError("exact US session is not the complete month-end session")
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx,
        actions=actions, failures=failures, decision_at=decision_at, policy=ObservationPolicy())
    observations = dataset.observations
    us_obs = observations.loc[observations.security_id.isin(us.security_id)].copy()
    visible = prices.loc[pd.to_datetime(prices.retrieved_at, utc=True).le(pd.Timestamp(decision_at))]
    scores = prepare_cross_section(us_obs, prices, decision_at=decision_at,
        segment_boundaries=detect_price_segments(visible, actions))
    latest = visible.loc[visible.status.eq("available")].sort_values(
        ["qualified_symbol", "trading_date", "retrieved_at"]).drop_duplicates("qualified_symbol", keep="last")
    latest["trading_date"] = pd.to_datetime(latest.trading_date).dt.date
    if set(us_obs.loc[us_obs.eligible, "qualified_symbol"]) - set(latest.loc[latest.trading_date.eq(session_date), "qualified_symbol"]):
        raise ReadinessError("model-ready prices are incomplete or stale for the exact session")
    if scores.empty:
        raise ReadinessError("frozen price score is unavailable")
    scores["price_percentile"] = scores.composite_score.rank(method="average", pct=True)
    price_rows = scores.merge(latest[["qualified_symbol", "adjusted_close", "trading_date"]], on="qualified_symbol")
    price_rows = price_rows.rename(columns={"adjusted_close": "decision_price", "trading_date": "price_date"})
    price_rows["model_ready"] = True
    price_rows = price_rows[["security_id", "qualified_symbol", "price_percentile", "decision_price", "model_ready", "price_date"]]
    cp = checkpoints.copy(); cp["updated_at"] = pd.to_datetime(cp.updated_at, utc=True, errors="coerce")
    cp = cp.loc[cp.updated_at.le(pd.Timestamp(decision_at))].sort_values("updated_at").drop_duplicates("security_id", keep="last")
    mapped = set(issuers.loc[pd.to_datetime(issuers.mapped_at, utc=True).le(pd.Timestamp(decision_at)), "security_id"])
    completed = set(cp.loc[cp.status.eq("completed"), "security_id"])
    permanent = set(cp.loc[cp.status.eq("permanent_failure"), "security_id"])
    unresolved = set(us.security_id) - (mapped & completed) - permanent
    if unresolved:
        raise ReadinessError("SEC issuer mappings or checkpoints are incomplete")
    dilution = _dilution_from_facts(facts, us.loc[us.security_id.isin(mapped & completed)], decision_at)
    if dilution.empty:
        raise ReadinessError("point-in-time dilution evidence is unavailable or stale")
    # The frozen price pipeline expresses model-ready histories in GBP, so USD/GBP
    # is genuinely required even though every prospective selection is US-listed.
    fx_needed = require_fx or bool(us.loc[~us.currency.eq("GBP")].shape[0])
    fx_ready = not fx_needed or bool(len(fx.loc[pd.to_datetime(fx.available_at, utc=True).le(pd.Timestamp(decision_at))
        & pd.to_datetime(fx.observed_on).dt.date.eq(session_date)]))
    after = _paths(research_db, production_db)
    if before != after:
        raise ReadinessError("database changed during read-only planning")
    readiness = {"complete_month_end_session": True, "fx_ready": fx_ready,
        "permanently_unmapped": len(permanent), "missing_dilution": int(len(price_rows) - len(dilution)),
        "database_fingerprints": {"research": {"before": asdict(before[0]), "after": asdict(after[0]), "unchanged": True},
          "production": {"before": asdict(before[1]), "after": asdict(after[1]), "unchanged": True}}}
    return price_rows, dilution, readiness


def plan_from_databases(*, research_db: Path, production_db: Path, decision_at: datetime,
                        session_date: date, now: datetime | None = None,
                        require_fx: bool = False) -> dict[str, Any]:
    now = _utc(now or datetime.now(timezone.utc)); decision_at = _utc(decision_at)
    if decision_at > now:
        raise ReadinessError("future decision timestamps are prohibited")
    if session_date != decision_at.date():
        raise ReadinessError("decision timestamp must use the exact US session date")
    prices, dilution, readiness = database_inputs(research_db=research_db,
        production_db=production_db, decision_at=decision_at, session_date=session_date,
        require_fx=require_fx)
    plan = build_plan(prices, dilution, decision_at=decision_at, generated_at=now,
        session_ready=readiness["complete_month_end_session"], fx_ready=readiness["fx_ready"])
    plan["command"] = "plan-prospective-us-shadow-from-db"
    plan["source"] = "authoritative_databases"
    plan["readiness"].update({k: v for k, v in readiness.items() if k != "database_fingerprints"})
    plan["database_fingerprints"] = readiness["database_fingerprints"]
    for selection in plan["proposed_paper_selections"]:
        growth = selection.get("diluted_share_growth")
        selection["finance_reason"] = (
            "Price evidence supplies 90% of the frozen score; lower point-in-time diluted-share growth supplies 10%."
            if growth is not None else "Dilution evidence unavailable; selection withheld.")
    before = _paths(research_db, production_db)
    token_payload = {k: plan[k] for k in ("plan_id", "decision_at", "generated_at", "vintage_month")}
    plan["expires_at"] = (now + PLAN_TTL).isoformat()
    plan["plan_identifier"] = _token(token_payload, *before)
    return plan


def build_plan(prices: pd.DataFrame, dilution: pd.DataFrame, *, decision_at: datetime,
               generated_at: datetime, session_ready: bool, fx_ready: bool) -> dict[str, Any]:
    validate_prospective_boundary(decision_at)
    eligible, selected = score_inputs(prices, dilution, decision_at=decision_at)
    readiness = {"complete_month_end_session": bool(session_ready), "fx_ready": bool(fx_ready),
                 "confirmed": bool(session_ready and fx_ready)}
    payload = _plan_payload(decision_at, eligible, selected, generated_at, readiness)
    fields = ["security_id", "qualified_symbol", "price_percentile", "dilution_percentile",
              "prospective_score", "decision_price", "price_date", "diluted_share_growth",
              "period_end", "filed_at", "retrieved_at", "provenance", "withholding_reasons"]
    return {"command": "plan-prospective-us-shadow", "mode": "strictly_read_only", "label": LABEL,
        **payload, "registration_timestamp": SPECIFICATION["registration_timestamp"],
        "eligible_count": len(eligible), "paper_selection_count": len(selected),
        "proposed_paper_selections": selected[fields].head(3).to_dict("records"),
        "ranking_families": ["price", "dilution"], "diagnostic_only_families":
        ["leverage", "profitability", "cash_flow", "growth", "valuation"],
        "production_publication": False, "broker_action": False, "validated": False}


def _schema(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS prospective_us_shadow_vintages(
      vintage_id VARCHAR PRIMARY KEY, strategy_version VARCHAR, vintage_month VARCHAR,
      decision_at TIMESTAMPTZ, configuration_hash VARCHAR, plan_id VARCHAR UNIQUE,
      manifest_json JSON, selections_json JSON, eligible_json JSON, created_at TIMESTAMPTZ,
      label VARCHAR, UNIQUE(strategy_version,vintage_month))""")
    db.execute("""CREATE TABLE IF NOT EXISTS prospective_us_shadow_cohorts(
      cohort_id VARCHAR PRIMARY KEY, vintage_id VARCHAR, strategy_version VARCHAR,
      horizon_sessions INTEGER, price_only_json JSON, eligible_baseline_json JSON,
      outcome_json JSON, evaluated_at TIMESTAMPTZ)""")


def create_from_plan(*, research_db: Path, production_db: Path, plan: dict[str, Any],
                     prices: pd.DataFrame, dilution: pd.DataFrame, authorization: str,
                     now: datetime) -> dict[str, Any]:
    if authorization != AUTHORIZATION_PHRASE:
        raise PermissionError("exact research-only authorization phrase is required")
    before_research, before_production = _paths(research_db, production_db)
    decision_at = datetime.fromisoformat(plan["decision_at"])
    rebuilt = build_plan(prices, dilution, decision_at=decision_at,
        generated_at=datetime.fromisoformat(plan["generated_at"]),
        session_ready=plan["readiness"]["complete_month_end_session"], fx_ready=plan["readiness"]["fx_ready"])
    locked_plan_fields = ("strategy_version", "configuration_hash", "decision_at", "vintage_month",
                          "generated_at", "eligible_ids", "selected_ids", "readiness", "plan_id")
    if any(plan.get(key) != rebuilt.get(key) for key in locked_plan_fields):
        raise ReadinessError("plan is modified or does not match current inputs")
    if now - datetime.fromisoformat(plan["generated_at"]) > PLAN_TTL:
        raise ReadinessError("plan has expired")
    if not rebuilt["readiness"]["confirmed"]:
        raise ReadinessError("complete month-end session and FX readiness are required")
    eligible, selected = score_inputs(prices, dilution, decision_at=decision_at)
    # Price-only comparator uses exactly the same eligible cohort and decision date.
    price_only = eligible.sort_values(["price_percentile", "qualified_symbol"], ascending=[False, True]).head(3)
    vintage_id = f"{STRATEGY_VERSION}:{decision_at:%Y-%m}"
    with duckdb.connect(str(research_db)) as db:
        _schema(db)
        incompatible = db.execute("SELECT configuration_hash FROM prospective_us_shadow_vintages WHERE strategy_version=? AND configuration_hash<>? LIMIT 1",
                                  [STRATEGY_VERSION, CONFIGURATION_HASH]).fetchone()
        if incompatible:
            raise ReadinessError("strategy version configuration is immutable; register a new version")
        existing = db.execute("SELECT configuration_hash FROM prospective_us_shadow_vintages WHERE strategy_version=? AND vintage_month=?",
                              [STRATEGY_VERSION, decision_at.strftime("%Y-%m")]).fetchone()
        if existing:
            if existing[0] != CONFIGURATION_HASH: raise ReadinessError("strategy version configuration is immutable")
            return {"command": "create-prospective-us-shadow", "status": "already_exists", "mutated": False}
        if db.execute("SELECT count(*) FROM prospective_us_shadow_vintages WHERE plan_id=?", [plan["plan_id"]]).fetchone()[0]:
            raise ReadinessError("plan has already been used")
        db.begin()
        try:
            manifest = {"specification": SPECIFICATION, "configuration_hash": CONFIGURATION_HASH}
            db.execute("INSERT INTO prospective_us_shadow_vintages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [vintage_id, STRATEGY_VERSION, decision_at.strftime("%Y-%m"), decision_at,
                 CONFIGURATION_HASH, plan["plan_id"], json.dumps(manifest),
                 selected.to_json(orient="records", date_format="iso"), eligible.to_json(orient="records", date_format="iso"), now, LABEL])
            for horizon in (126, 252):
                db.execute("INSERT INTO prospective_us_shadow_cohorts VALUES (?,?,?,?,?,?,?,?)",
                    [f"{vintage_id}:{horizon}", vintage_id, STRATEGY_VERSION, horizon,
                     price_only.to_json(orient="records", date_format="iso"),
                     eligible.to_json(orient="records", date_format="iso"), None, None])
            db.commit()
        except Exception:
            db.rollback(); raise
    if fingerprint(production_db) != before_production:
        raise ReadinessError("production database changed during research creation")
    return {"command": "create-prospective-us-shadow", "status": "created", "mutated": True,
        "vintage_id": vintage_id, "paper_selection_count": len(selected), "cohorts": [126, 252],
        "production_unchanged": True, "production_published": False, "broker_action": False}


def create_from_database_plan(*, research_db: Path, production_db: Path,
                              plan_identifier: str, authorization: str,
                              now: datetime) -> dict[str, Any]:
    """Consume only a fresh, unmodified DB plan token and rebuild its inputs."""
    now = _utc(now)
    if authorization != AUTHORIZATION_PHRASE:
        raise PermissionError("exact research-only authorization phrase is required")
    before = _paths(research_db, production_db)
    # Preserve strategy/month idempotency after the first authorized transaction
    # changes the research fingerprint. This branch never trusts token selections.
    try:
        raw = plan_identifier.split(".", 1)[0]
        envelope = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        vintage_month = str(envelope["plan"]["vintage_month"])
        prior_plan_id = str(envelope["plan"]["plan_id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        vintage_month = prior_plan_id = ""
    if vintage_month:
        with duckdb.connect(str(research_db), read_only=True) as db:
            if "prospective_us_shadow_vintages" in _table_names(db) and db.execute(
                "SELECT count(*) FROM prospective_us_shadow_vintages WHERE strategy_version=? AND vintage_month=? AND plan_id=?",
                [STRATEGY_VERSION, vintage_month, prior_plan_id]).fetchone()[0]:
                return {"command": "create-prospective-us-shadow", "status": "already_exists",
                        "mutated": False}
    token = _decode_token(plan_identifier, *before)
    generated_at = datetime.fromisoformat(token["generated_at"])
    if now < generated_at or now - generated_at > PLAN_TTL:
        raise ReadinessError("plan has expired or is not immediately preceding")
    decision_at = datetime.fromisoformat(token["decision_at"])
    rebuilt = plan_from_databases(research_db=research_db, production_db=production_db,
        decision_at=decision_at, session_date=decision_at.date(), now=generated_at)
    if rebuilt["plan_identifier"] != plan_identifier or rebuilt["plan_id"] != token["plan_id"]:
        raise ReadinessError("plan is modified or databases changed after planning")
    prices, dilution, _ = database_inputs(research_db=research_db, production_db=production_db,
        decision_at=decision_at, session_date=decision_at.date())
    creation_plan = dict(rebuilt)
    creation_plan["readiness"] = {
        "complete_month_end_session": rebuilt["readiness"]["complete_month_end_session"],
        "fx_ready": rebuilt["readiness"]["fx_ready"],
        "confirmed": rebuilt["readiness"]["confirmed"],
    }
    result = create_from_plan(research_db=research_db, production_db=production_db,
        plan=creation_plan, prices=prices, dilution=dilution, authorization=authorization, now=now)
    return {**result, "plan_identifier_consumed": True}


def readiness(*, research_db: Path, production_db: Path, decision_at: datetime,
              session_date: date, now: datetime | None = None,
              require_fx: bool = False) -> dict[str, Any]:
    """Bounded dashboard state; never invents selections when evidence is unavailable."""
    now = _utc(now or datetime.now(timezone.utc))
    month_end = date(session_date.year, session_date.month,
                     calendar.monthrange(session_date.year, session_date.month)[1])
    if now.date() < month_end:
        state, detail, plan = "waiting_for_month_end", "month-end data is not yet available", None
    else:
        try:
            plan = plan_from_databases(research_db=research_db, production_db=production_db,
                decision_at=decision_at, session_date=session_date, now=now, require_fx=require_fx)
            if plan["readiness"].get("missing_dilution"):
                state, detail = "bounded_withholding", "securities without dilution evidence are withheld"
            elif not plan["readiness"]["confirmed"]:
                state, detail = "not_ready", "required session or FX evidence is unavailable"
            else:
                state, detail = "ready_for_authorized_creation", "read-only plan is ready for deliberate authorization"
        except ReadinessError as exc:
            plan = None
            message = str(exc)
            state = ("refresh_required" if "price" in message or "catalogue" in message
                     else "bounded_withholding" if "dilution" in message
                     else "not_ready")
            detail = message
    existing = False
    with duckdb.connect(str(research_db), read_only=True) as db:
        if "prospective_us_shadow_vintages" in _table_names(db):
            existing = bool(db.execute("SELECT count(*) FROM prospective_us_shadow_vintages WHERE strategy_version=? AND vintage_month=?",
                [STRATEGY_VERSION, session_date.strftime("%Y-%m")]).fetchone()[0])
    if existing:
        state, detail, plan = "paper_vintage_created", "research-only paper vintage already exists", None
    return {"command": "prospective-us-shadow-readiness", "state": state, "detail": detail,
        "decision_at": decision_at.isoformat(), "session_date": session_date.isoformat(),
        "paper_selection_count": 0 if plan is None else plan["paper_selection_count"],
        "proposed_paper_selections": [] if plan is None else plan["proposed_paper_selections"],
        "production_publication": False, "broker_action": False, "automatic_creation": False}


def status(*, research_db: Path, production_db: Path, now: datetime | None = None) -> dict[str, Any]:
    before = _paths(research_db, production_db)
    rows: list[tuple] = []; cohorts: list[tuple] = []
    with duckdb.connect(str(research_db), read_only=True) as db:
        tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "prospective_us_shadow_vintages" in tables:
            rows = db.execute("SELECT vintage_month,decision_at,configuration_hash,selections_json FROM prospective_us_shadow_vintages ORDER BY decision_at").fetchall()
            cohorts = db.execute("SELECT horizon_sessions,count(*),count(outcome_json) FROM prospective_us_shadow_cohorts GROUP BY horizon_sessions ORDER BY horizon_sessions").fetchall()
    if before != _paths(research_db, production_db): raise ReadinessError("database changed during read-only status")
    maturity = {str(h): {"total": int(total), "completed": int(done), "immature": int(total-done)} for h,total,done in cohorts}
    captured = _utc(now or datetime.now(timezone.utc))
    current_state = ("waiting_for_month_end" if captured.date() < date(2026, 10, 31)
                     else "refresh_required")
    return {"command": "prospective-us-shadow-status", "label": "PROSPECTIVE PAPER RESEARCH ONLY",
        "warning": "NOT VALIDATED — NOT INVESTMENT ADVICE.", "strategy_version": STRATEGY_VERSION,
        "configuration_hash": CONFIGURATION_HASH, "registration_timestamp": SPECIFICATION["registration_timestamp"],
        "next_eligible_month_end": SPECIFICATION["first_permissible_vintage"], "current_readiness": current_state,
        "vintages": len(rows), "completed_vintages": min((v["completed"] for v in maturity.values()), default=0),
        "minimum_completed_vintages": 12, "paper_selection_count": sum(len(json.loads(r[3])) for r in rows),
        "cohort_maturity": maturity, "promotion_gates": SPECIFICATION["promotion_requirements"],
        "evidence_immature": True, "production_unchanged": True}


def evaluate_maturity(sessions: pd.DatetimeIndex, decision_at: datetime, horizon: int,
                      returns: dict[str, float], selected: list[str], price_only: list[str],
                      eligible: list[str]) -> dict[str, Any]:
    maturity = maturity_session(sessions, decision_at, horizon)
    if maturity is None:
        return {"matured": False, "reason": "exact_session_not_reached"}
    required = set(selected) | set(price_only) | set(eligible)
    if not required or not required.issubset(returns):
        return {"matured": True, "complete": False, "reason": "incomplete_outcomes"}
    mean = lambda members: float(np.mean([returns[x] for x in members]))
    overlay, price, baseline = mean(selected), mean(price_only), mean(eligible)
    return {"matured": True, "complete": True, "maturity_date": str(maturity.date()),
        "overlay_return": overlay, "price_only_return": price, "equal_weight_return": baseline,
        "incremental_vs_price_only": overlay-price, "excess_vs_equal_weight": overlay-baseline}
