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
