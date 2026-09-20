from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .market_data import MarketDataRepository


class WatchlistRepository:
    """Mutable personal research notes, separate from immutable predictions."""

    def __init__(self, repository: MarketDataRepository):
        self.repository = repository
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_notes (
                    ticker VARCHAR PRIMARY KEY,
                    note VARCHAR NOT NULL,
                    added_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """
            )

    def upsert(self, ticker: str, note: str) -> dict[str, Any]:
        normalized_ticker = ticker.strip().upper()
        normalized_note = note.strip()
        if not normalized_ticker:
            raise ValueError("ticker is required")
        if not normalized_note:
            raise ValueError("research note is required")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.repository.connect() as connection:
            connection.execute(
                """
                INSERT INTO watchlist_notes
                    (ticker, note, added_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    note = EXCLUDED.note,
                    updated_at = EXCLUDED.updated_at
                """,
                [normalized_ticker, normalized_note, now, now],
            )
        return self.get(normalized_ticker)

    def get(self, ticker: str) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                """
                SELECT ticker, note, added_at, updated_at
                FROM watchlist_notes
                WHERE ticker = ?
                """,
                [ticker.strip().upper()],
            ).fetchone()
        if row is None:
            return None
        return {
            "ticker": row[0],
            "note": row[1],
            "added_at": row[2],
            "updated_at": row[3],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT ticker, note, added_at, updated_at
                FROM watchlist_notes
                ORDER BY updated_at DESC, ticker
                """
            ).fetchall()
        return [
            {
                "ticker": row[0],
                "note": row[1],
                "added_at": row[2],
                "updated_at": row[3],
            }
            for row in rows
        ]

    def remove(self, ticker: str) -> bool:
        normalized_ticker = ticker.strip().upper()
        with self.repository.connect() as connection:
            existed = connection.execute(
                "SELECT 1 FROM watchlist_notes WHERE ticker = ?",
                [normalized_ticker],
            ).fetchone()
            if existed is not None:
                connection.execute(
                    "DELETE FROM watchlist_notes WHERE ticker = ?",
                    [normalized_ticker],
                )
        return existed is not None
