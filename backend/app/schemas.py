from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str


class RankingItem(BaseModel):
    rank: int = Field(ge=1)
    ticker: str
    company: str
    score: float = Field(ge=0, le=100)
    thesis: str
    risk: str


class RankingResponse(BaseModel):
    as_of: date
    model: str
    is_demo: bool
    disclaimer: str
    rankings: list[RankingItem]


class PublishedRankingItem(BaseModel):
    rank: int = Field(ge=1)
    ticker: str
    company: str
    momentum_126d: float
    evidence: str
    risk: str


class PublishedMacroObservation(BaseModel):
    observation_id: str
    series_id: Literal["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"]
    metric: str
    value: float
    unit: str
    frequency: Literal["daily", "monthly"]
    observation_date: date
    available_at: datetime
    retrieved_at: datetime
    source_name: str
    source_url: str


class PublishedMacroContext(BaseModel):
    captured_at: datetime
    status: Literal["complete", "partial", "missing"]
    expected_series: list[str]
    missing_series: list[str]
    observations: list[PublishedMacroObservation]


class PublishedRankingResponse(BaseModel):
    vintage_id: str
    as_of: date
    created_at: datetime
    strategy: str
    strategy_version: str
    is_demo: Literal[False] = False
    disclaimer: str
    macro_context: PublishedMacroContext | None = None
    rankings: list[PublishedRankingItem]


class PredictionOutcomeItem(BaseModel):
    ticker: str
    rank: int = Field(ge=1)
    status: Literal["pending", "completed"]
    entry_date: date | None
    exit_date: date | None
    realized_return: float | None
    available_post_signal_closes: int = Field(ge=0)
    required_post_signal_closes: int = Field(ge=2)


class PredictionOutcomeResponse(BaseModel):
    vintage_id: str
    as_of_date: date
    horizon_trading_days: int = Field(ge=1)
    status: Literal["pending", "completed"]
    completed_predictions: int = Field(ge=0)
    total_predictions: int = Field(ge=1)
    mean_realized_return: float | None
    outcomes: list[PredictionOutcomeItem]


class RankingHistoryItem(BaseModel):
    vintage_id: str
    as_of_date: date
    created_at: datetime
    strategy: str
    strategy_version: str
    tickers: list[str]
    status: Literal["pending", "completed"]
    completed_predictions: int = Field(ge=0)
    total_predictions: int = Field(ge=1)
    mean_realized_return: float | None


class RankingHistoryResponse(BaseModel):
    vintages: list[RankingHistoryItem]


class WatchlistNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class WatchlistItem(BaseModel):
    ticker: str
    company: str
    note: str
    added_at: datetime
    updated_at: datetime


class WatchlistResponse(BaseModel):
    items: list[WatchlistItem]


class IngestionRunResponse(BaseModel):
    run_id: str
    source: str
    status: str
    rows_written: int
    completed_at: datetime | None
    error: str | None


class DataStatusResponse(BaseModel):
    universe_size: int
    covered_tickers: int
    row_count: int
    first_date: date | None
    last_date: date | None
    duplicate_rows: int
    forward_horizon_trading_days: int
    last_run: IngestionRunResponse | None


class EvidenceItemResponse(BaseModel):
    evidence_id: str
    ticker: str
    evidence_type: Literal["fundamental", "filing", "news", "macro"]
    source_name: str
    source_url: str
    title: str
    summary: str
    published_at: datetime
    source_updated_at: datetime | None
    retrieved_at: datetime
    content_hash: str


class TickerEvidenceResponse(BaseModel):
    ticker: str
    as_of: datetime
    evidence_type: Literal["fundamental", "filing", "news", "macro"]
    status: Literal["fresh", "stale", "missing", "failed"]
    max_age_days: int = Field(ge=1)
    error: str | None
    items: list[EvidenceItemResponse]


class FundamentalFactResponse(BaseModel):
    fact_id: str
    ticker: str
    metric: Literal[
        "revenue",
        "net_income",
        "eps_diluted",
        "assets",
        "liabilities",
        "cash",
    ]
    taxonomy: str
    concept: str
    unit: str
    value: float
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    source_url: str


class TickerFundamentalsResponse(BaseModel):
    ticker: str
    company: str
    as_of: datetime
    status: Literal["complete", "partial", "missing"]
    expected_metrics: list[str]
    missing_metrics: list[str]
    facts: list[FundamentalFactResponse]


class MacroObservationResponse(BaseModel):
    observation_id: str
    series_id: Literal["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"]
    metric: str
    value: float
    unit: str
    frequency: Literal["daily", "monthly"]
    observation_date: date
    available_at: datetime
    retrieved_at: datetime
    source_name: str
    source_url: str
    freshness: Literal["fresh", "stale"]


class MacroSnapshotResponse(BaseModel):
    as_of: datetime
    status: Literal["complete", "partial", "missing"]
    expected_series: list[str]
    missing_series: list[str]
    stale_series: list[str]
    observations: list[MacroObservationResponse]
