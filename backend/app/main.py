from datetime import date, datetime, timedelta, timezone
from hmac import compare_digest
import time
import uuid

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .evidence import EvidenceRepository, EvidenceType
from .fred_macro import FRED_SERIES
from .fundamentals import FundamentalRepository
from .global_universe import GlobalUniverseRepository
from .global_market_data import GlobalMarketDataRepository
from .global_research import GlobalResearchRepository
from .research_evaluation import ResearchEvaluationRepository
from .macro import MacroRepository
from .market_data import MarketDataRepository
from .monthly_cycle import (
    ProductionBackupError,
    ProductionPreflightError,
    production_stages,
    run_production_monthly_cycle,
)
from .outcomes import evaluate_prediction_vintage
from .prediction_store import PredictionVintageStore
from .schemas import (
    DataStatusResponse,
    EvidenceItemResponse,
    FundamentalFactResponse,
    HealthResponse,
    MacroObservationResponse,
    MacroSnapshotResponse,
    PredictionOutcomeResponse,
    PublishedRankingItem,
    PublishedRankingResponse,
    RankingHistoryItem,
    RankingHistoryResponse,
    RankingItem,
    RankingResponse,
    WatchlistItem,
    WatchlistNoteRequest,
    WatchlistResponse,
    TickerEvidenceResponse,
    TickerFundamentalsResponse,
)
from .universe import FORWARD_HORIZON_TRADING_DAYS, UNIVERSE
from .watchlist import WatchlistRepository
from .eodhd_ingestion import EODHDClient, EODHDIngestion, EODHDLimits
from .model_readiness import assess_model_readiness
from .research_scoring import assess_research_scoring
from .operations import AssessmentJobs, OperationHistory, research_health, safe_error
from .database_backup import backup_status
from .shadow_portfolios import (create_shadow_vintage, evaluate_matured_shadows,
                                plan_shadow_vintage, shadow_status)

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

PUBLIC_API_PATHS = {"/api/v1/health"}
PRIVATE_DOCUMENTATION_PATHS = {"/docs", "/openapi.json", "/redoc"}


class OperationRequest(BaseModel):
    decision_at: datetime
    confirmation: str = ""
    expected_session_dates: dict[str, date] = Field(default_factory=dict)
    latest_required_fx_date: date | None = None
    plan_id: str | None = None

assessment_jobs: AssessmentJobs | None = None
_shadow_plans: dict[str, tuple[float, OperationRequest]] = {}


def _assessment_jobs() -> AssessmentJobs:
    """Initialize persistence only when an operator explicitly starts/reads a job."""
    global assessment_jobs
    if assessment_jobs is None:
        history = OperationHistory(settings.research_database_path, settings.database_path)
        assessment_jobs = AssessmentJobs(history=history)
    return assessment_jobs


def _validate_sessions(body: OperationRequest) -> None:
    if set(body.expected_session_dates) != {"US", "LSE", "TO", "XETRA", "PA"}:
        raise HTTPException(422, detail={"code": "invalid_explicit_sessions",
            "message": "Provide one valid date for each documented region: US, LSE, TO, XETRA and PA."})


def _research_ingestion() -> EODHDIngestion:
    token = settings.eodhd_api_token
    return EODHDIngestion(settings.research_database_path, settings.database_path,
        EODHDClient(token.get_secret_value() if token else "offline-read-only", EODHDLimits()))


def _authorize(request: Request, configured, phrase: str) -> None:
    supplied = request.headers.get("X-SignalLens-Operation-Authorization", "")
    if configured is None or not supplied or not compare_digest(supplied, configured.get_secret_value()):
        raise HTTPException(403, detail={"code": "operation_not_authorized", "message": phrase})


def _redacted(call):
    try:
        return call()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, detail=safe_error()) from None


@app.middleware("http")
async def authenticate_private_api(request: Request, call_next):
    token = settings.api_token
    is_private_path = (
        request.url.path.startswith("/api/v1/")
        or request.url.path in PRIVATE_DOCUMENTATION_PATHS
    )
    requires_authentication = (
        is_private_path
        and request.url.path not in PUBLIC_API_PATHS
        and token is not None
    )
    if requires_authentication:
        scheme, _, supplied_token = request.headers.get(
            "Authorization", ""
        ).partition(" ")
        expected_token = token.get_secret_value()
        if (
            scheme.lower() != "bearer"
            or not supplied_token
            or not compare_digest(supplied_token, expected_token)
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing bearer token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    if settings.staging_mode and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(status_code=409, content={"detail": {
            "code": "staging_read_only", "message": "Staging mode prohibits all writes."}})
    return await call_next(request)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@app.get("/api/v1/operations/health")
def operations_health() -> dict:
    return research_health(settings.research_database_path, settings.database_path,
                           scheduler_enabled=settings.scheduler_enabled,
                           configured=bool(settings.app_name and settings.environment))


@app.get("/api/v1/operations/backups/status")
def operations_backup_status() -> dict:
    return _redacted(lambda: backup_status(
        settings.backup_path, configured="backup_path" in settings.model_fields_set))


def _recent_operations_read_only(limit: int) -> list[dict]:
    """Read an existing journal without creating schema as a side effect."""
    path = settings.research_database_path
    if not path.is_file():
        return []
    with duckdb.connect(str(path), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if "research_operations" not in tables:
            return []
        columns = ["operation_id", "operation_type", "state", "created_at", "started_at",
                   "finished_at", "progress", "summary", "strategy_version",
                   "configuration_version", "failure_class"]
        rows = connection.execute(
            "SELECT * FROM research_operations ORDER BY created_at DESC LIMIT ?",
            [min(max(limit, 1), 20)],
        ).fetchall()
    return [{key: value.isoformat() if isinstance(value, datetime) else value
             for key, value in zip(columns, row)} for row in rows]


@app.get("/api/v1/operations/summary")
def operations_summary() -> dict:
    """Bounded operator-first summary; diagnostics remain separate endpoints."""
    health = research_health(settings.research_database_path, settings.database_path,
                             scheduler_enabled=settings.scheduler_enabled)
    backup = backup_status(settings.backup_path,
                           configured="backup_path" in settings.model_fields_set)
    coverage = _research_ingestion().coverage()
    history: list[dict] = []
    if settings.research_database_path.is_file():
        try:
            history = _recent_operations_read_only(settings.operations_history_limit)
        except Exception:
            history = []
    running = any(item["state"] in {"queued", "running"} for item in history)
    price_dates = [item.get("latest_trading_date") for item in coverage.get("price_coverage", [])
                   if item.get("latest_trading_date") is not None]
    fx_dates = [item.get("latest_observation_date") for item in coverage.get("fx_coverage", [])
                if item.get("latest_observation_date") is not None]
    progress = coverage.get("security_progress") or {}
    selected = sum(int(item.get("securities", 0))
                   for item in coverage.get("catalogue_selections", []))
    latest_run = coverage.get("latest_run")
    running = running or bool(latest_run and latest_run.get("status") in {"queued", "running"})
    attention = (not health["database_isolation_confirmed"]
                 or backup["status"] not in {"validated", "not_configured"}
                 or int(progress.get("permanently_failed", 0)) > 0)
    return {"label": "RESEARCH ONLY — NOT INVESTMENT ADVICE",
            "overall": "Running" if running else "Attention required" if attention else "Healthy",
            "latest_successful_refresh": (latest_run.get("finished_at") if latest_run and
                latest_run.get("status") in {"completed", "completed_with_failures", "partial"} else "not_recorded"),
            "latest_price_date": max(price_dates) if price_dates else "not_recorded",
            "latest_fx_date": max(fx_dates) if fx_dates else "not_recorded",
            "counts": {"selected": selected, "model_ready": "not_assessed",
                       "withheld": "not_assessed"},
            "security_progress": progress or "not_recorded",
            "latest_ingestion": latest_run or "not_recorded",
            "readiness": "not_assessed", "scoring": "not_assessed",
            "journal_evidence": "recorded" if history else "not_recorded",
            "next_scheduled_operation": None, "backup": backup,
            "month_end_plan": {"state": "not_confirmed", "proposed_selections": []},
            "shadow_cohorts": [], "recent_operations": history[:20],
            "production_publishing_available": False}


@app.get("/api/v1/operations/coverage")
def operations_coverage() -> dict:
    return _redacted(lambda: _research_ingestion().coverage())


@app.post("/api/v1/operations/assessments/{kind}", status_code=202)
def operations_start_assessment(kind: str, decision_at: datetime) -> dict:
    if kind == "model-readiness":
        work = lambda: assess_model_readiness(research_db=settings.research_database_path,
            production_db=settings.database_path, decision_at=decision_at, sample_limit=10)
    elif kind == "research-scoring":
        work = lambda: assess_research_scoring(research_db=settings.research_database_path,
            production_db=settings.database_path, decision_at=decision_at)
    else:
        raise HTTPException(404, detail=safe_error("assessment_not_found", "Assessment type is unavailable."))
    return _assessment_jobs().start(kind, work)


@app.get("/api/v1/operations/assessments/jobs/{job_id}")
def operations_assessment_job(job_id: str) -> dict:
    result = _assessment_jobs().get(job_id)
    if result is None:
        raise HTTPException(404, detail=safe_error("job_not_found", "Assessment job was not found."))
    return result


@app.delete("/api/v1/operations/assessments/jobs/{job_id}")
def operations_cancel_assessment(job_id: str) -> dict:
    result = _assessment_jobs().cancel(job_id)
    if result is None:
        raise HTTPException(404, detail=safe_error("job_not_found", "Assessment job was not found."))
    return result


@app.get("/api/v1/operations/incremental-refresh/plan")
def operations_refresh_plan(decision_at: datetime, verbose: bool = False) -> dict:
    return _redacted(lambda: _research_ingestion().plan_refresh(
        as_of=decision_at, include_request_details=verbose))


@app.post("/api/v1/operations/incremental-refresh/execute")
def operations_refresh_execute(body: OperationRequest, request: Request) -> dict:
    _authorize(request, settings.refresh_authorization_token,
               "Separate incremental-refresh authorization is required.")
    if body.confirmation != "REFRESH RESEARCH DATA" or settings.eodhd_api_token is None:
        raise HTTPException(409, detail={"code": "confirmation_failed",
            "message": "Refresh confirmation and a configured provider entitlement are required."})
    return _redacted(lambda: _research_ingestion().refresh(retrieved_at=body.decision_at))


@app.post("/api/v1/operations/shadow/plan")
def operations_shadow_plan(body: OperationRequest) -> dict:
    _validate_sessions(body)
    result = _redacted(lambda: plan_shadow_vintage(research_db=settings.research_database_path,
        production_db=settings.database_path, cutoff=body.decision_at,
        expected_session_dates={k: str(v) for k, v in body.expected_session_dates.items()},
        latest_required_fx_date=str(body.latest_required_fx_date) if body.latest_required_fx_date else None))
    if not result.get("month_end_readiness", {}).get("confirmed"):
        return result
    plan_id = uuid.uuid4().hex
    _shadow_plans.clear()
    _shadow_plans[plan_id] = (time.monotonic(), body)
    return {**result, "plan_id": plan_id}


@app.post("/api/v1/operations/shadow/create")
def operations_shadow_create(body: OperationRequest, request: Request) -> dict:
    _validate_sessions(body)
    _authorize(request, settings.shadow_authorization_token,
               "Separate research-shadow authorization is required.")
    if body.confirmation != "CREATE RESEARCH SHADOW":
        raise HTTPException(409, detail={"code": "confirmation_failed",
            "message": "Type CREATE RESEARCH SHADOW to authorize this research write."})
    prior = _shadow_plans.pop(body.plan_id, None) if body.plan_id else None
    same_plan = prior is not None and (
        prior[1].decision_at == body.decision_at
        and prior[1].expected_session_dates == body.expected_session_dates
        and prior[1].latest_required_fx_date == body.latest_required_fx_date
    )
    if prior is None or time.monotonic() - prior[0] > 600 or not same_plan:
        raise HTTPException(409, detail={"code": "preceding_plan_required",
            "message": "A successful immediately preceding month-end plan is required."})
    if settings.environment.lower() in {"development", "test", "testing"} and body.decision_at.strftime("%Y-%m") == "2026-09":
        raise HTTPException(409, detail={"code": "protected_vintage",
            "message": "The September 2026 vintage is protected from development and test creation."})
    return _redacted(lambda: create_shadow_vintage(research_db=settings.research_database_path,
        production_db=settings.database_path, cutoff=body.decision_at, authorized=True,
        expected_session_dates={k: str(v) for k, v in body.expected_session_dates.items()},
        latest_required_fx_date=str(body.latest_required_fx_date) if body.latest_required_fx_date else None,
        require_month_end_readiness=True))


@app.get("/api/v1/operations/shadow/status")
def operations_shadow_status() -> dict:
    return _redacted(lambda: shadow_status(research_db=settings.research_database_path,
                                           production_db=settings.database_path))


@app.post("/api/v1/operations/shadow/evaluate-matured")
def operations_shadow_evaluate(body: OperationRequest, request: Request) -> dict:
    _authorize(request, settings.refresh_authorization_token,
               "Research-write authorization is required for evaluation.")
    if body.confirmation != "EVALUATE MATURED SHADOWS":
        raise HTTPException(409, detail={"code": "confirmation_failed", "message": "Deliberate confirmation is required."})
    return _redacted(lambda: evaluate_matured_shadows(research_db=settings.research_database_path,
        production_db=settings.database_path, as_of=body.decision_at))


@app.post("/api/v1/admin/monthly-cycle")
def trigger_monthly_cycle() -> dict:
    """Run in the API service so the sole DuckDB writer owns the mounted volume."""
    repository = MarketDataRepository(settings.database_path)
    try:
        return run_production_monthly_cycle(
            repository, production_stages(repository, settings=settings), settings=settings
        )
    except ProductionPreflightError as exc:
        raise HTTPException(status_code=503, detail=exc.report) from None
    except ProductionBackupError as exc:
        # Do not reflect the original exception text: paths and configuration
        # values can contain operational secrets.
        raise HTTPException(
            status_code=503,
            detail={
                "command": "monthly_cycle",
                "status": "blocked",
                "failed_stage": "backup",
                "error": {"code": exc.error_code, "message": "Fresh backup failed"},
            },
        ) from None


@app.get("/api/v1/data/status", response_model=DataStatusResponse)
def data_status() -> DataStatusResponse:
    repository = MarketDataRepository(settings.database_path)
    repository.seed_universe(UNIVERSE)
    return DataStatusResponse(
        **repository.status(), forward_horizon_trading_days=FORWARD_HORIZON_TRADING_DAYS
    )


@app.get("/api/v1/universe/coverage")
def global_universe_coverage() -> dict:
    """Read-only coverage for the research-only global security master."""
    return GlobalUniverseRepository(settings.database_path).coverage()


@app.get("/api/v1/universe/market-data-coverage")
def global_market_data_coverage() -> dict:
    """Read-only price, FX, and failure coverage for shadow research."""
    return GlobalMarketDataRepository(settings.database_path).coverage()


@app.get("/api/v1/research/global-multifactor/status")
def global_multifactor_status() -> dict:
    """Read-only coverage; mutations are available only through explicit CLI commands."""
    return GlobalResearchRepository(settings.database_path).status()


@app.get("/api/v1/research/global-multifactor/latest")
def latest_global_multifactor() -> dict:
    """Return the latest shadow vintage without touching production predictions."""
    vintage = GlobalResearchRepository(settings.database_path).latest_vintage()
    if vintage is None:
        return {"status": "unavailable", "label": "SHADOW RESEARCH — NOT A PRODUCTION RANKING", "candidates": []}
    return {"status": "available", "label": "SHADOW RESEARCH — NOT A PRODUCTION RANKING", **vintage}


@app.get("/api/v1/research/global-evaluation/status")
def global_evaluation_status() -> dict:
    """Authenticated read-only Milestone 10 results from the isolated database."""
    return ResearchEvaluationRepository(
        settings.research_database_path, production_path=settings.database_path
    ).status()


@app.get("/api/v1/rankings/latest", response_model=PublishedRankingResponse)
def latest_rankings() -> PublishedRankingResponse:
    repository = MarketDataRepository(settings.database_path)
    stored = PredictionVintageStore(repository).get_latest("momentum_126d")
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="No published ranking vintage is available",
        )

    companies = {security.ticker: security.company for security in UNIVERSE}
    rankings = [
        PublishedRankingItem(
            rank=item["rank"],
            ticker=item["ticker"],
            company=companies.get(item["ticker"], item["ticker"]),
            momentum_126d=item["score"],
            evidence=(
                f"Adjusted-price momentum over 126 trading days: "
                f"{item['score']:.1%}."
            ),
            risk=(
                "Momentum can reverse sharply; this signal excludes fundamentals, "
                "news, valuation, liquidity, and personal suitability."
            ),
        )
        for item in stored["predictions"]
    ]
    return PublishedRankingResponse(
        vintage_id=stored["vintage_id"],
        as_of=stored["as_of_date"],
        created_at=stored["created_at"],
        strategy=stored["strategy_name"],
        strategy_version=stored["strategy_version"],
        disclaimer=(
            "Research output only. This historical-price signal is not investment "
            "advice and does not guarantee future growth."
        ),
        macro_context=stored["metadata"].get("macro_context"),
        fundamental_context=stored["metadata"].get("fundamental_context"),
        evidence_context=stored["metadata"].get("evidence_context"),
        rankings=rankings,
    )


@app.get(
    "/api/v1/rankings/latest/outcomes",
    response_model=PredictionOutcomeResponse,
)
def latest_ranking_outcomes() -> PredictionOutcomeResponse:
    repository = MarketDataRepository(settings.database_path)
    stored = PredictionVintageStore(repository).get_latest("momentum_126d")
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="No published ranking vintage is available",
        )
    return PredictionOutcomeResponse(
        **evaluate_prediction_vintage(
            repository,
            stored["vintage_id"],
            FORWARD_HORIZON_TRADING_DAYS,
        )
    )


@app.get("/api/v1/rankings/history", response_model=RankingHistoryResponse)
def ranking_history() -> RankingHistoryResponse:
    repository = MarketDataRepository(settings.database_path)
    store = PredictionVintageStore(repository)
    vintages = []
    for vintage_id in store.list_vintage_ids("momentum_126d"):
        stored = store.get(vintage_id)
        outcome = evaluate_prediction_vintage(
            repository,
            vintage_id,
            FORWARD_HORIZON_TRADING_DAYS,
        )
        vintages.append(
            RankingHistoryItem(
                vintage_id=vintage_id,
                as_of_date=stored["as_of_date"],
                created_at=stored["created_at"],
                strategy=stored["strategy_name"],
                strategy_version=stored["strategy_version"],
                tickers=[
                    item["ticker"]
                    for item in stored["predictions"]
                ],
                status=outcome["status"],
                completed_predictions=outcome["completed_predictions"],
                total_predictions=outcome["total_predictions"],
                mean_realized_return=outcome["mean_realized_return"],
            )
        )
    return RankingHistoryResponse(vintages=vintages)


@app.get("/api/v1/macro/latest", response_model=MacroSnapshotResponse)
def latest_macro(
    as_of: datetime | None = None,
) -> MacroSnapshotResponse:
    requested_as_of = as_of or datetime.now(timezone.utc)
    observations = MacroRepository(
        MarketDataRepository(settings.database_path)
    ).point_in_time(requested_as_of)

    expected = list(FRED_SERIES)
    observed = {item["series_id"] for item in observations}
    missing = [series_id for series_id in expected if series_id not in observed]
    stale_after_days = {"daily": 7, "monthly": 62}
    stale: list[str] = []
    response_items = []

    as_of_date = requested_as_of.date()
    for item in observations:
        age_days = (as_of_date - item["observation_date"]).days
        freshness = (
            "fresh"
            if age_days <= stale_after_days[item["frequency"]]
            else "stale"
        )
        if freshness == "stale":
            stale.append(item["series_id"])
        response_items.append(
            MacroObservationResponse(**item, freshness=freshness)
        )

    status = (
        "missing"
        if not observations
        else "complete"
        if not missing
        else "partial"
    )
    return MacroSnapshotResponse(
        as_of=requested_as_of,
        status=status,
        expected_series=expected,
        missing_series=missing,
        stale_series=stale,
        observations=response_items,
    )


@app.get(
    "/api/v1/fundamentals/{ticker}",
    response_model=TickerFundamentalsResponse,
)
def ticker_fundamentals(
    ticker: str,
    as_of: datetime | None = None,
) -> TickerFundamentalsResponse:
    normalized = ticker.strip().upper()
    companies = {security.ticker: security.company for security in UNIVERSE}
    if normalized not in companies:
        raise HTTPException(status_code=400, detail="Ticker is not in the universe")

    requested_as_of = as_of or datetime.now(timezone.utc)
    facts = FundamentalRepository(
        MarketDataRepository(settings.database_path)
    ).point_in_time(normalized, requested_as_of)

    expected_metrics = [
        "revenue",
        "net_income",
        "eps_diluted",
        "assets",
        "liabilities",
        "cash",
    ]
    observed = {fact["metric"] for fact in facts}
    missing = [
        metric for metric in expected_metrics if metric not in observed
    ]
    status = (
        "missing"
        if not facts
        else "complete"
        if not missing
        else "partial"
    )
    return TickerFundamentalsResponse(
        ticker=normalized,
        company=companies[normalized],
        as_of=requested_as_of,
        status=status,
        expected_metrics=expected_metrics,
        missing_metrics=missing,
        facts=[FundamentalFactResponse(**fact) for fact in facts],
    )


@app.get(
    "/api/v1/evidence/{ticker}",
    response_model=TickerEvidenceResponse,
)
def ticker_evidence(
    ticker: str,
    evidence_type: EvidenceType = "filing",
    as_of: datetime | None = None,
    max_age_days: int = Query(default=90, ge=1, le=3650),
) -> TickerEvidenceResponse:
    normalized = ticker.strip().upper()
    known_tickers = {security.ticker for security in UNIVERSE}
    if normalized not in known_tickers:
        raise HTTPException(status_code=400, detail="Ticker is not in the universe")

    requested_as_of = as_of or datetime.now(timezone.utc)
    repository = EvidenceRepository(
        MarketDataRepository(settings.database_path)
    )
    availability = repository.availability(
        normalized,
        evidence_type,
        requested_as_of,
        timedelta(days=max_age_days),
    )
    items = repository.point_in_time(
        normalized,
        requested_as_of,
        evidence_type,
    )
    return TickerEvidenceResponse(
        ticker=normalized,
        as_of=requested_as_of,
        evidence_type=evidence_type,
        status=availability["status"],
        max_age_days=max_age_days,
        error=availability["error"],
        items=[EvidenceItemResponse(**item) for item in items],
    )


@app.get("/api/v1/watchlist", response_model=WatchlistResponse)
def watchlist() -> WatchlistResponse:
    repository = WatchlistRepository(
        MarketDataRepository(settings.database_path)
    )
    companies = {security.ticker: security.company for security in UNIVERSE}
    return WatchlistResponse(
        items=[
            WatchlistItem(
                **item,
                company=companies.get(item["ticker"], item["ticker"]),
            )
            for item in repository.list()
        ]
    )


@app.put("/api/v1/watchlist/{ticker}", response_model=WatchlistItem)
def save_watchlist_note(
    ticker: str,
    request: WatchlistNoteRequest,
) -> WatchlistItem:
    normalized = ticker.strip().upper()
    companies = {security.ticker: security.company for security in UNIVERSE}
    if normalized not in companies:
        raise HTTPException(status_code=400, detail="Ticker is not in the universe")
    item = WatchlistRepository(
        MarketDataRepository(settings.database_path)
    ).upsert(normalized, request.note)
    return WatchlistItem(**item, company=companies[normalized])


@app.delete("/api/v1/watchlist/{ticker}")
def delete_watchlist_note(ticker: str) -> dict[str, bool]:
    removed = WatchlistRepository(
        MarketDataRepository(settings.database_path)
    ).remove(ticker)
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist ticker not found")
    return {"deleted": True}


@app.get("/api/v1/rankings/demo", response_model=RankingResponse)
def demo_rankings() -> RankingResponse:
    return RankingResponse(
        as_of=date(2026, 9, 19), model="foundation-demo-v0", is_demo=True,
        disclaimer="Demonstration data only. Not investment advice or a live model output.",
        rankings=[
            RankingItem(rank=1, ticker="NOVA", company="Nova Systems", score=82, thesis="Synthetic momentum and quality example.", risk="Synthetic valuation risk."),
            RankingItem(rank=2, ticker="GRID", company="GridWorks", score=76, thesis="Synthetic infrastructure-demand example.", risk="Synthetic project-delay risk."),
            RankingItem(rank=3, ticker="FLOW", company="Flow Analytics", score=71, thesis="Synthetic recurring-revenue example.", risk="Synthetic competition risk."),
        ],
    )
