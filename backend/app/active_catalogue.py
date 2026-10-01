"""Authoritative read-only selection of the initialized EODHD catalogue."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb
import pandas as pd


@dataclass(frozen=True)
class ActiveCatalogue:
    retrieval_id: str
    retrieved_at: datetime
    listings: pd.DataFrame


def select_active_catalogue(
    connection: duckdb.DuckDBPyConnection, *, as_of: datetime | None = None
) -> ActiveCatalogue | None:
    """Return the latest completed retrieval using the model-readiness rules.

    This is the single selection boundary for consumers of the initialized
    EODHD catalogue.  A universe snapshot is a downstream research artefact,
    not the authority for whether a provider listing is in the active catalogue.
    """
    if as_of is None:
        retrieval = connection.execute(
            """SELECT retrieval_id, retrieved_at FROM security_master_retrievals
               WHERE status='completed'
               ORDER BY retrieved_at DESC, retrieval_id DESC LIMIT 1"""
        ).fetchone()
    else:
        retrieval = connection.execute(
            """SELECT retrieval_id, retrieved_at FROM security_master_retrievals
               WHERE status='completed' AND retrieved_at<=?
               ORDER BY retrieved_at DESC, retrieval_id DESC LIMIT 1""",
            [as_of],
        ).fetchone()
    if retrieval is None:
        return None
    listings = connection.execute(
        """SELECT security_id, qualified_symbol,
                  UPPER(TRIM(primary_exchange)) AS region,
                  UPPER(TRIM(currency)) AS currency,
                  (active AND instrument_type IN ('common_stock','ordinary_share')) AS eligible
           FROM security_listings WHERE retrieval_id=?
           ORDER BY qualified_symbol, security_id""",
        [retrieval[0]],
    ).fetchdf()
    return ActiveCatalogue(str(retrieval[0]), retrieval[1], listings)


def eodhd_ticker(value: Any) -> str:
    """Apply the catalogue pipeline's provider-ticker normalization."""
    return str(value or "").strip().upper()
