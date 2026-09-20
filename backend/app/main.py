from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .market_data import MarketDataRepository
from .outcomes import evaluate_prediction_vintage
from .prediction_store import PredictionVintageStore
from .schemas import (
    DataStatusResponse,
    HealthResponse,
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
)
from .universe import FORWARD_HORIZON_TRADING_DAYS, UNIVERSE
from .watchlist import WatchlistRepository

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["GET", "PUT", "DELETE"], allow_headers=["*"])


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
