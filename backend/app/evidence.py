from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from .market_data import MarketDataRepository


EvidenceType = Literal["fundamental", "filing", "news", "macro"]
FetchStatus = Literal["completed", "missing", "failed"]
AvailabilityStatus = Literal["fresh", "stale", "missing", "failed"]

EVIDENCE_TYPES = {"fundamental", "filing", "news", "macro"}
FETCH_STATUSES = {"completed", "missing", "failed"}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class EvidenceItem:
    ticker: str
    evidence_type: EvidenceType
    source_name: str
    source_url: str
    title: str
    summary: str
    published_at: datetime
    retrieved_at: datetime
    source_updated_at: datetime | None = None
    evidence_id: str | None = None

    def normalized(self) -> "EvidenceItem":
        published_at = _utc_naive(self.published_at)
        retrieved_at = _utc_naive(self.retrieved_at)
        source_updated_at = (
            None
            if self.source_updated_at is None
            else _utc_naive(self.source_updated_at)
        )
        ticker = self.ticker.strip().upper()
        evidence_type = self.evidence_type.strip().lower()
        source_name = self.source_name.strip()
        source_url = self.source_url.strip()
        title = self.title.strip()
        summary = self.summary.strip()

        if not ticker:
            raise ValueError("Ticker is required")
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"Unsupported evidence type: {evidence_type}")
        if not source_name:
            raise ValueError("Source name is required")
        if not source_url.startswith(("https://", "http://")):
            raise ValueError("Source URL must be an HTTP(S) URL")
        if not title:
            raise ValueError("Title is required")
        if published_at > retrieved_at:
            raise ValueError("Evidence cannot be retrieved before publication")
        if source_updated_at is not None and source_updated_at > retrieved_at:
            raise ValueError("Source update cannot occur after retrieval")

        return EvidenceItem(
            ticker=ticker,
            evidence_type=evidence_type,  # type: ignore[arg-type]
            source_name=source_name,
            source_url=source_url,
            title=title,
            summary=summary,
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_updated_at=source_updated_at,
            evidence_id=self.evidence_id or str(uuid4()),
        )


class EvidenceRepository:
    def __init__(self, market_data: MarketDataRepository):
        self.market_data = market_data
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.market_data.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS evidence_items (
                    evidence_id VARCHAR PRIMARY KEY,
                    ticker VARCHAR NOT NULL,
                    evidence_type VARCHAR NOT NULL,
                    source_name VARCHAR NOT NULL,
                    source_url VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    summary VARCHAR NOT NULL,
                    published_at TIMESTAMP NOT NULL,
                    source_updated_at TIMESTAMP,
                    retrieved_at TIMESTAMP NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    inserted_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_fetches (
                    fetch_id VARCHAR PRIMARY KEY,
                    ticker VARCHAR NOT NULL,
                    evidence_type VARCHAR NOT NULL,
                    source_name VARCHAR NOT NULL,
                    requested_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP NOT NULL,
                    status VARCHAR NOT NULL,
                    item_count INTEGER NOT NULL,
                    error VARCHAR
                );
            """)

    def save(self, item: EvidenceItem) -> dict:
        item = item.normalized()
        fingerprint = sha256(
            "\n".join(
                [
                    item.ticker,
                    item.evidence_type,
                    item.source_name,
                    item.source_url,
                    item.title,
                    item.summary,
                    item.published_at.isoformat(),
                    "" if item.source_updated_at is None else item.source_updated_at.isoformat(),
                ]
            ).encode("utf-8")
        ).hexdigest()
        inserted_at = datetime.now(timezone.utc).replace(tzinfo=None)

        with self.market_data.connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_items VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    item.evidence_id,
                    item.ticker,
                    item.evidence_type,
                    item.source_name,
                    item.source_url,
                    item.title,
                    item.summary,
                    item.published_at,
                    item.source_updated_at,
                    item.retrieved_at,
                    fingerprint,
                    inserted_at,
                ],
            )
        return {
            "evidence_id": item.evidence_id,
            "ticker": item.ticker,
            "evidence_type": item.evidence_type,
            "content_hash": fingerprint,
            "published_at": item.published_at,
            "retrieved_at": item.retrieved_at,
        }

    def record_fetch(
        self,
        ticker: str,
        evidence_type: EvidenceType,
        source_name: str,
        requested_at: datetime,
        completed_at: datetime,
        status: FetchStatus,
        item_count: int = 0,
        error: str | None = None,
    ) -> str:
        ticker = ticker.strip().upper()
        evidence_type = evidence_type.strip().lower()
        source_name = source_name.strip()
        status = status.strip().lower()
        requested_at = _utc_naive(requested_at)
        completed_at = _utc_naive(completed_at)

        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"Unsupported evidence type: {evidence_type}")
        if status not in FETCH_STATUSES:
            raise ValueError(f"Unsupported fetch status: {status}")
        if completed_at < requested_at:
            raise ValueError("Fetch cannot complete before it starts")
        if item_count < 0:
            raise ValueError("Item count cannot be negative")
        if status == "completed" and item_count < 1:
            raise ValueError("Completed fetch must contain at least one item")
        if status != "completed" and item_count != 0:
            raise ValueError("Missing or failed fetch cannot contain items")
        if status == "failed" and not error:
            raise ValueError("Failed fetch must include an error")

        fetch_id = str(uuid4())
        with self.market_data.connect() as connection:
            connection.execute(
                "INSERT INTO evidence_fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    fetch_id,
                    ticker,
                    evidence_type,
                    source_name,
                    requested_at,
                    completed_at,
                    status,
                    item_count,
                    error,
                ],
            )
        return fetch_id

    def point_in_time(
        self,
        ticker: str,
        as_of: datetime,
        evidence_type: EvidenceType | None = None,
    ) -> list[dict]:
        ticker = ticker.strip().upper()
        as_of = _utc_naive(as_of)
        parameters: list[object] = [ticker, as_of, as_of]
        type_clause = ""
        if evidence_type is not None:
            normalized_type = evidence_type.strip().lower()
            if normalized_type not in EVIDENCE_TYPES:
                raise ValueError(f"Unsupported evidence type: {normalized_type}")
            type_clause = "AND evidence_type = ?"
            parameters.append(normalized_type)

        with self.market_data.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT evidence_id, ticker, evidence_type, source_name,
                       source_url, title, summary, published_at,
                       source_updated_at, retrieved_at, content_hash
                FROM evidence_items
                WHERE ticker = ?
                  AND published_at <= ?
                  AND retrieved_at <= ?
                  {type_clause}
                ORDER BY published_at DESC, retrieved_at DESC
                """,
                parameters,
            ).fetchall()

        columns = (
            "evidence_id",
            "ticker",
            "evidence_type",
            "source_name",
            "source_url",
            "title",
            "summary",
            "published_at",
            "source_updated_at",
            "retrieved_at",
            "content_hash",
        )
        return [dict(zip(columns, row)) for row in rows]

    def availability(
        self,
        ticker: str,
        evidence_type: EvidenceType,
        as_of: datetime,
        max_age: timedelta,
    ) -> dict:
        ticker = ticker.strip().upper()
        normalized_type = evidence_type.strip().lower()
        as_of = _utc_naive(as_of)
        items = self.point_in_time(ticker, as_of, normalized_type)  # type: ignore[arg-type]
        if items:
            latest = items[0]
            effective_at = latest["source_updated_at"] or latest["published_at"]
            age = as_of - effective_at
            status: AvailabilityStatus = "fresh" if age <= max_age else "stale"
            return {"status": status, "age": age, "item": latest, "error": None}

        with self.market_data.connect() as connection:
            fetch = connection.execute(
                """
                SELECT status, completed_at, error
                FROM evidence_fetches
                WHERE ticker = ? AND evidence_type = ? AND completed_at <= ?
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                [ticker, normalized_type, as_of],
            ).fetchone()

        if fetch is not None and fetch[0] == "failed":
            return {
                "status": "failed",
                "age": as_of - fetch[1],
                "item": None,
                "error": fetch[2],
            }
        return {
            "status": "missing",
            "age": None if fetch is None else as_of - fetch[1],
            "item": None,
            "error": None,
        }
