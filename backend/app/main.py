from datetime import date, datetime, timedelta, timezone
from hmac import compare_digest

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .evidence import EvidenceRepository, EvidenceType
from .fred_macro import FRED_SERIES
from .fundamentals import FundamentalRepository
from .macro import MacroRepository
from .market_data import MarketDataRepository
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

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["GET", "PUT", "DELETE"], allow_headers=["*"])

PUBLIC_API_PATHS = {"/api/v1/health"}


@app.middleware("http")
async def authenticate_private_api(request: Request, call_next):
    token = settings.api_token
    requires_authentication = (
        request.url.path.startswith("/api/v1/")
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
    return await call_next(request)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@app.get("/api/v1/data/status", response_model=DataStatusResponse)
def data_status() -> DataStatusResponse:
    repository = MarketDataRepository(settings.database_path)
    repository.seed_universe(UNIVERSE)
    return DataStatusResponse(
        **repository.status(), forward_horizon_trading_days=FORWARD_HORIZON_TRADING_DAYS
    )


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
