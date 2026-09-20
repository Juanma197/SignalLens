from __future__ import annotations

import json
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

import pandas as pd

from .market_data import MarketDataRepository


class PredictionVintageStore:
    """Append-only storage for published research prediction vintages."""

    _schema_lock = Lock()
    _initialized_databases: set[str] = set()

    def __init__(self, repository: MarketDataRepository):
        self.repository = repository
        database_key = str(repository.path.resolve())
        with self._schema_lock:
            if database_key not in self._initialized_databases:
                self._ensure_schema()
                self._initialized_databases.add(database_key)

    def _ensure_schema(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_vintages (
                    vintage_id VARCHAR PRIMARY KEY,
                    strategy_name VARCHAR NOT NULL,
                    strategy_version VARCHAR NOT NULL,
                    as_of_date DATE NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    metadata_json VARCHAR NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prediction_records (
                    vintage_id VARCHAR NOT NULL,
                    ticker VARCHAR NOT NULL,
                    rank INTEGER NOT NULL,
                    score DOUBLE NOT NULL,
                    PRIMARY KEY (vintage_id, ticker),
                    FOREIGN KEY (vintage_id)
                        REFERENCES prediction_vintages(vintage_id)
                );
                """
            )

    def publish(
        self,
        strategy_name: str,
        strategy_version: str,
        as_of_date: date | pd.Timestamp,
        predictions: pd.DataFrame,
        score_column: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append one immutable ranking vintage and return its identifier."""
        if not strategy_name.strip() or not strategy_version.strip():
            raise ValueError("strategy name and version are required")
        required = {"ticker", "rank", score_column}
        missing = required - set(predictions.columns)
        if missing:
            raise ValueError(
                f"Missing prediction columns: {', '.join(sorted(missing))}"
            )
        if predictions.empty:
            raise ValueError("Cannot publish an empty prediction vintage")
        if predictions["ticker"].duplicated().any():
            raise ValueError("Prediction vintage contains duplicate tickers")
        if predictions["rank"].duplicated().any():
            raise ValueError("Prediction vintage contains duplicate ranks")
        if (predictions["rank"] < 1).any():
            raise ValueError("Prediction ranks must be positive")
        if predictions[score_column].isna().any():
            raise ValueError("Prediction scores cannot be missing")

        vintage_id = str(uuid4())
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        normalized_date = pd.Timestamp(as_of_date).date()
        metadata_json = json.dumps(
            metadata or {},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        records = [
            [
                vintage_id,
                str(row.ticker),
                int(row.rank),
                float(getattr(row, score_column)),
            ]
            for row in predictions.itertuples(index=False)
        ]

        with self.repository.connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO prediction_vintages
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        vintage_id,
                        strategy_name,
                        strategy_version,
                        normalized_date,
                        created_at,
                        metadata_json,
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO prediction_records
                    VALUES (?, ?, ?, ?)
                    """,
                    records,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return vintage_id

    def get(self, vintage_id: str) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            vintage = connection.execute(
                """
                SELECT strategy_name, strategy_version, as_of_date,
                       created_at, metadata_json
                FROM prediction_vintages
                WHERE vintage_id = ?
                """,
                [vintage_id],
            ).fetchone()
            if vintage is None:
                return None
            records = connection.execute(
                """
                SELECT ticker, rank, score
                FROM prediction_records
                WHERE vintage_id = ?
                ORDER BY rank
                """,
                [vintage_id],
            ).fetchall()

        return {
            "vintage_id": vintage_id,
            "strategy_name": vintage[0],
            "strategy_version": vintage[1],
            "as_of_date": vintage[2],
            "created_at": vintage[3],
            "metadata": json.loads(vintage[4]),
            "predictions": [
                {"ticker": row[0], "rank": row[1], "score": row[2]}
                for row in records
            ],
        }

    def get_latest(self, strategy_name: str | None = None) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            if strategy_name is None:
                row = connection.execute(
                    """
                    SELECT vintage_id
                    FROM prediction_vintages
                    ORDER BY as_of_date DESC, created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT vintage_id
                    FROM prediction_vintages
                    WHERE strategy_name = ?
                    ORDER BY as_of_date DESC, created_at DESC
                    LIMIT 1
                    """,
                    [strategy_name],
                ).fetchone()

        return None if row is None else self.get(row[0])

    def list_vintage_ids(self, strategy_name: str | None = None) -> list[str]:
        with self.repository.connect() as connection:
            if strategy_name is None:
                rows = connection.execute(
                    """
                    SELECT vintage_id
                    FROM prediction_vintages
                    ORDER BY as_of_date DESC, created_at DESC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT vintage_id
                    FROM prediction_vintages
                    WHERE strategy_name = ?
                    ORDER BY as_of_date DESC, created_at DESC
                    """,
                    [strategy_name],
                ).fetchall()
        return [row[0] for row in rows]

    def count(self) -> int:
        with self.repository.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM prediction_vintages"
                ).fetchone()[0]
            )
