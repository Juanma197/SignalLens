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


class PublishedRankingResponse(BaseModel):
    vintage_id: str
    as_of: date
    created_at: datetime
    strategy: str
    strategy_version: str
    is_demo: Literal[False] = False
    disclaimer: str
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
