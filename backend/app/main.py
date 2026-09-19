from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .schemas import HealthResponse, RankingItem, RankingResponse

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@app.get("/api/v1/rankings/demo", response_model=RankingResponse)
def demo_rankings() -> RankingResponse:
    return RankingResponse(
        as_of=date(2026, 9, 19),
        model="foundation-demo-v0",
        is_demo=True,
        disclaimer="Demonstration data only. Not investment advice or a live model output.",
        rankings=[
            RankingItem(rank=1, ticker="NOVA", company="Nova Systems", score=82, thesis="Synthetic momentum and quality example.", risk="Synthetic valuation risk."),
            RankingItem(rank=2, ticker="GRID", company="GridWorks", score=76, thesis="Synthetic infrastructure-demand example.", risk="Synthetic project-delay risk."),
            RankingItem(rank=3, ticker="FLOW", company="Flow Analytics", score=71, thesis="Synthetic recurring-revenue example.", risk="Synthetic competition risk."),
        ],
    )

