from datetime import date
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

