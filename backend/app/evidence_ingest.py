from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Protocol

from .evidence import EvidenceItem, EvidenceRepository


class EvidenceProvider(Protocol):
    name: str

    def download(
        self,
        tickers: Iterable[str],
        retrieved_at: datetime,
        **kwargs,
    ) -> dict[str, list[EvidenceItem]]: ...


def ingest_evidence(
    repository: EvidenceRepository,
    provider: EvidenceProvider,
    tickers: Iterable[str],
    retrieved_at: datetime | None = None,
    **provider_options,
) -> dict:
    normalized_tickers = tuple(
        dict.fromkeys(ticker.strip().upper() for ticker in tickers)
    )
    if not normalized_tickers:
        raise ValueError("At least one ticker is required")

    requested_at = retrieved_at or datetime.now(timezone.utc)
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)

    try:
        downloaded = provider.download(
            normalized_tickers,
            retrieved_at=requested_at,
            **provider_options,
        )
    except Exception as exc:
        completed_at = datetime.now(timezone.utc)
        if completed_at < requested_at:
            completed_at = requested_at
        error = str(exc) or type(exc).__name__
        for ticker in normalized_tickers:
            repository.record_fetch(
                ticker=ticker,
                evidence_type="filing",
                source_name=provider.name,
                requested_at=requested_at,
                completed_at=completed_at,
                status="failed",
                error=error,
            )
        raise

    fetched = 0
    stored = 0
    duplicates = 0
    failed = 0
    provider_errors: dict[str, str] = getattr(provider, "errors", {})
    completed_at = datetime.now(timezone.utc)
    if completed_at < requested_at:
        completed_at = requested_at

    for ticker in normalized_tickers:
        if ticker in provider_errors:
            failed += 1
            repository.record_fetch(
                ticker=ticker,
                evidence_type="filing",
                source_name=provider.name,
                requested_at=requested_at,
                completed_at=completed_at,
                status="failed",
                error=provider_errors[ticker],
            )
            continue

        items = downloaded.get(ticker, [])
        fetched += len(items)
        for item in items:
            normalized = item.normalized()
            if normalized.evidence_id and repository.contains(
                normalized.evidence_id
            ):
                duplicates += 1
                continue
            repository.save(normalized)
            stored += 1

        repository.record_fetch(
            ticker=ticker,
            evidence_type="filing",
            source_name=provider.name,
            requested_at=requested_at,
            completed_at=completed_at,
            status="completed" if items else "missing",
            item_count=len(items),
        )

    return {
        "source": provider.name,
        "tickers": len(normalized_tickers),
        "fetched": fetched,
        "stored": stored,
        "duplicates": duplicates,
        "failed": failed,
        "retrieved_at": requested_at,
    }
