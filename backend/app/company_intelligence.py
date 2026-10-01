"""Provider-neutral, point-in-time company intelligence primitives.

This module is deliberately storage-free.  It normalizes caller-supplied records
(including offline fixtures), and requires a separate explicit authorization at
the network and persistence boundaries implemented by any future adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Literal, Mapping


FeatureFamily = Literal[
    "growth", "profitability", "free_cash_flow", "leverage",
    "dilution", "valuation", "event_date", "transactions", "news",
]
ObservationKind = Literal["numeric", "event", "transaction", "news"]


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp is required")
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceProvenance:
    provider: str
    publisher: str
    source_url: str
    source_type: Literal["regulator", "issuer", "government", "news"]
    license_or_terms: str
    accession_or_document_id: str

    def validate(self) -> None:
        if not all((self.provider, self.publisher, self.license_or_terms,
                    self.accession_or_document_id)):
            raise ValueError("complete source provenance is required")
        if not self.source_url.startswith("https://"):
            raise ValueError("a canonical HTTPS source URL is required")


@dataclass(frozen=True)
class CompanyObservation:
    observation_id: str
    security_id: str
    family: FeatureFamily
    metric: str
    kind: ObservationKind
    value: float | str
    unit: str | None
    currency: str | None
    period_start: datetime | None
    period_end: datetime | None
    public_at: datetime
    retrieved_at: datetime
    source: SourceProvenance
    filing_type: str | None = None
    amendment_number: int = 0
    supersedes_id: str | None = None
    expires_at: datetime | None = None

    def validated(self) -> "CompanyObservation":
        public_at, retrieved_at = utc(self.public_at), utc(self.retrieved_at)
        if public_at > retrieved_at:
            raise ValueError("future-information leakage: public_at exceeds retrieved_at")
        if self.period_end is not None and public_at == utc(self.period_end):
            raise ValueError("fiscal-period end cannot be used as public_at")
        if self.kind == "numeric" and not self.unit:
            raise ValueError("numeric observations require a unit")
        if self.unit and self.unit.upper() in {"USD", "EUR", "GBP", "JPY", "CAD"}:
            if self.currency != self.unit.upper():
                raise ValueError("monetary unit and ISO currency must agree")
        if self.currency and (len(self.currency) != 3 or self.currency != self.currency.upper()):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        if self.expires_at is not None and utc(self.expires_at) < public_at:
            raise ValueError("expires_at cannot precede public_at")
        self.source.validate()
        return replace(self, public_at=public_at, retrieved_at=retrieved_at)


def normalize_observations(records: Iterable[CompanyObservation]) -> tuple[CompanyObservation, ...]:
    """Validate, deduplicate, and retain amendments without rewriting history."""
    by_id: dict[str, CompanyObservation] = {}
    for record in records:
        item = record.validated()
        previous = by_id.get(item.observation_id)
        if previous is not None and previous != item:
            raise ValueError(f"conflicting duplicate observation: {item.observation_id}")
        by_id[item.observation_id] = item
    ids = set(by_id)
    for item in by_id.values():
        if item.supersedes_id and item.supersedes_id not in ids:
            raise ValueError(f"missing superseded observation: {item.supersedes_id}")
    return tuple(sorted(by_id.values(), key=lambda x: (x.public_at, x.observation_id)))


def point_in_time(records: Iterable[CompanyObservation], as_of: datetime) -> tuple[CompanyObservation, ...]:
    """Return one known version per security/metric/period at ``as_of``."""
    boundary = utc(as_of)
    eligible = [r for r in normalize_observations(records)
                if r.public_at <= boundary and r.retrieved_at <= boundary]
    selected: dict[tuple[object, ...], CompanyObservation] = {}
    for item in eligible:
        key = (item.security_id, item.metric, item.period_start, item.period_end)
        current = selected.get(key)
        if current is None or (item.amendment_number, item.public_at, item.observation_id) > (
            current.amendment_number, current.public_at, current.observation_id
        ):
            selected[key] = item
    return tuple(sorted(selected.values(), key=lambda x: (x.security_id, x.metric)))


def assess_news(item: CompanyObservation, as_of: datetime, max_age_days: int = 30) -> str:
    if item.family != "news":
        raise ValueError("news assessment requires a news observation")
    boundary = utc(as_of)
    if item.public_at > boundary or item.retrieved_at > boundary:
        return "future"
    expires = item.expires_at or item.public_at + timedelta(days=max_age_days)
    return "stale" if boundary > expires else "usable"


def require_live_authorization(live_authorized: bool, action: str) -> None:
    if live_authorized is not True:
        raise PermissionError(f"explicit live authorization required before {action}")


def load_offline_fixture(path: Path, *, offline_fixture_mode: bool) -> object:
    """Read a local fixture only when the caller explicitly selects offline mode."""
    if offline_fixture_mode is not True:
        raise PermissionError("offline_fixture_mode must be explicitly enabled")
    return json.loads(path.read_text(encoding="utf-8"))


def file_fingerprint(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_files_immutable(before: Mapping[Path, str]) -> None:
    changed = [str(path) for path, digest in before.items()
               if not path.exists() or file_fingerprint(path) != digest]
    if changed:
        raise RuntimeError("database immutability violated: " + ", ".join(changed))
