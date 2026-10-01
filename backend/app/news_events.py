"""Provider-neutral, point-in-time company-event primitives.

Research context only: this module has deliberately no dependency on scoring,
rankings, candidates, publishing, or network clients.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import duckdb

from .sec_capability import fingerprint
from .sec_ingestion import validate_paths

MAX_SUMMARY_CHARS = 600
MAX_HEADLINE_CHARS = 240
MAX_OUTPUT_EVENTS = 25
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class EventCategory(StrEnum):
    EARNINGS = "earnings_release"
    GUIDANCE = "guidance_change"
    ACQUISITION_DISPOSAL = "acquisition_or_disposal"
    MANAGEMENT = "management_change"
    CAPITAL = "capital_raise_or_buyback"
    DIVIDEND = "dividend"
    MATERIAL_CONTRACT = "material_contract"
    LITIGATION_REGULATORY = "litigation_or_regulatory_action"
    DISTRESS = "bankruptcy_or_distress"
    PRODUCT = "product_announcement"
    ANALYST_RATING = "analyst_rating_change"
    GOVERNMENT_CONTRACT = "government_contract"
    INSIDER = "insider_transaction"
    POLITICAL_TRADING = "political_or_government_trading"
    GENERAL = "general_company_news"

class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"

class Freshness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    FUTURE = "future"


@dataclass(frozen=True)
class IssuerIdentity:
    company_id: str
    company_name: str
    ticker: str | None = None
    ticker_valid_from: str | None = None
    ticker_valid_to: str | None = None
    cik: str | None = None
    lei: str | None = None
    isin: str | None = None


def aware(value: datetime | str | None, field: str, *, required: bool = True) -> datetime | None:
    if value is None:
        if required: raise ValueError(f"{field} is required")
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def sanitize_text(value: str, limit: int) -> str:
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", value or "", flags=re.I | re.S)
    value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    value = "".join(" " if unicodedata.category(c).startswith("C") else c for c in value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical URL must be HTTP(S)")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/": path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return urlunsplit((parsed.scheme.lower(), host + port, path, urlencode(sorted(query)), ""))


def content_hash(headline: str, summary: str) -> str:
    normalized = sanitize_text(f"{headline} {summary}", MAX_HEADLINE_CHARS + MAX_SUMMARY_CHARS).casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def near_duplicate_key(headline: str) -> str:
    words = re.findall(r"[a-z0-9]+", sanitize_text(headline, MAX_HEADLINE_CHARS).casefold())
    return hashlib.sha256(" ".join(sorted(set(words))).encode()).hexdigest()[:20]


def deduplicate(events: list["NewsEvent"]) -> tuple[list["NewsEvent"], list[dict[str, str]]]:
    """Prefer external IDs, then exact content; report but retain near duplicates."""
    kept: list[NewsEvent] = []
    exact: dict[tuple[str, str], str] = {}
    groups: list[dict[str, str]] = []
    for item in sorted(events, key=lambda x: (x.published_at, x.event_id)):
        key = (item.issuer.company_id, f"external:{item.external_id}" if item.external_id else item.duplicate_hash)
        if key in exact:
            groups.append({"event_id":item.event_id, "duplicate_of":exact[key], "kind":"exact"})
            continue
        nearby = next((prior for prior in kept if prior.issuer.company_id == item.issuer.company_id
                       and prior.near_duplicate_group == item.near_duplicate_group), None)
        if nearby: groups.append({"event_id":item.event_id, "duplicate_of":nearby.event_id, "kind":"near"})
        exact[key] = item.event_id
        kept.append(item)
    return kept, groups


def freshness(published_at: datetime, decision_at: datetime) -> Freshness:
    published, decision = aware(published_at, "published_at"), aware(decision_at, "decision_at")
    days = (decision - published).total_seconds() / 86400
    if days < 0: return Freshness.FUTURE
    if days <= 7: return Freshness.FRESH
    if days <= 90: return Freshness.AGING
    return Freshness.STALE


@dataclass(frozen=True)
class NewsEvent:
    event_id: str
    issuer: IssuerIdentity
    category: EventCategory
    headline: str
    summary: str
    source_name: str
    source_url: str
    published_at: datetime
    retrieved_at: datetime
    announced_at: datetime | None = None
    occurred_at: datetime | None = None
    external_id: str | None = None
    filing_accession: str | None = None
    corrects_event_id: str | None = None
    language: str = "en"
    source_jurisdiction: str = "US"
    provenance: str = "official"
    license_classification: str = "metadata_and_bounded_summary"
    issuer_match_confidence: float = 1.0
    direction: str = "unknown"
    materiality_evidence: str = "unspecified"
    novelty: str = "new"
    source_authority: str = "unknown"
    affected_horizon: str = "unknown"

    def __post_init__(self) -> None:
        publication = aware(self.published_at, "published_at")
        retrieval = aware(self.retrieved_at, "retrieved_at")
        announcement = aware(self.announced_at, "announced_at", required=False)
        occurrence = aware(self.occurred_at, "occurred_at", required=False)
        if publication > retrieval: raise ValueError("published_at cannot be after retrieved_at")
        if not 0 <= self.issuer_match_confidence <= 1: raise ValueError("issuer match confidence out of range")
        object.__setattr__(self, "headline", sanitize_text(self.headline, MAX_HEADLINE_CHARS))
        object.__setattr__(self, "summary", sanitize_text(self.summary, MAX_SUMMARY_CHARS))
        object.__setattr__(self, "source_url", canonical_url(self.source_url))
        object.__setattr__(self, "published_at", publication)
        object.__setattr__(self, "retrieved_at", retrieval)
        object.__setattr__(self, "announced_at", announcement)
        object.__setattr__(self, "occurred_at", occurrence)

    @property
    def duplicate_hash(self) -> str: return content_hash(self.headline, self.summary)
    @property
    def near_duplicate_group(self) -> str: return near_duplicate_key(self.headline)

    def available_at(self, decision_at: datetime) -> bool:
        boundary = aware(decision_at, "decision_at")
        return self.published_at <= boundary and self.retrieved_at <= boundary


def match_issuer(claim: dict[str, str], candidates: list[IssuerIdentity], at: datetime) -> IssuerIdentity:
    """Match durable IDs first; ticker alone requires an effective-date interval."""
    matches: list[IssuerIdentity] = []
    for candidate in candidates:
        durable = any(claim.get(k) and claim[k] == getattr(candidate, k) for k in ("company_id", "cik", "lei", "isin"))
        ticker = claim.get("ticker") and claim["ticker"].upper() == (candidate.ticker or "").upper()
        valid = ticker and candidate.ticker_valid_from and candidate.ticker_valid_to and (
            candidate.ticker_valid_from <= at.date().isoformat() <= candidate.ticker_valid_to)
        if durable or valid: matches.append(candidate)
    unique = {item.company_id: item for item in matches}
    if len(unique) != 1: raise ValueError("ambiguous or missing issuer identity")
    return next(iter(unique.values()))


SEC_ITEM_CATEGORIES = {
    "1.01": EventCategory.MATERIAL_CONTRACT, "1.02": EventCategory.MATERIAL_CONTRACT,
    "2.01": EventCategory.ACQUISITION_DISPOSAL, "2.04": EventCategory.DISTRESS,
    "2.05": EventCategory.DISTRESS, "2.06": EventCategory.DISTRESS,
    "2.02": EventCategory.EARNINGS, "3.02": EventCategory.CAPITAL,
    "5.02": EventCategory.MANAGEMENT, "7.01": EventCategory.GENERAL,
    "8.01": EventCategory.GENERAL,
}


def extract_sec_events(rows: list[dict[str, Any]], retrieved_at: datetime) -> list[NewsEvent]:
    result = []
    for row in rows:
        if row.get("form") not in {"8-K", "8-K/A"}: continue
        published = aware(row.get("public_at"), "public_at")
        items = [x.strip() for x in str(row.get("items") or "").split(",") if x.strip()]
        categories = sorted({SEC_ITEM_CATEGORIES[x] for x in items if x in SEC_ITEM_CATEGORIES})
        if not categories: categories = [EventCategory.GENERAL]
        accession = str(row["accession_number"])
        for category in categories:
            result.append(NewsEvent(event_id=f"sec:{accession}:{category}",
                issuer=IssuerIdentity(str(row["security_id"]), str(row.get("issuer_name") or row["security_id"]),
                    ticker=row.get("ticker"), cik=row.get("cik")), category=category,
                headline=f"SEC {row['form']} filing", summary="",
                source_name="SEC EDGAR", source_url=str(row["source_endpoint"]),
                published_at=published, retrieved_at=retrieved_at, external_id=accession,
                filing_accession=accession, corrects_event_id=row.get("amends_accession"),
                provenance="official_regulatory_filing", license_classification="public_record_metadata",
                source_authority="primary", materiality_evidence="8-K filing",
                novelty="amendment" if row["form"] == "8-K/A" else "new"))
    return sorted(result, key=lambda x: (x.published_at, x.event_id))


SOURCE_CAPABILITIES = [
 {"family":"official regulatory filings","regions":"jurisdiction-specific; SEC confirmed US","historical_depth":"SEC metadata varies; local database only assessed","publication_timestamps":"acceptance/public time where preserved","corrections":"amended filings/accessions","issuer_identifiers":"CIK; filing accession","access":"official APIs/bulk files","request_limits":"authority-specific","cost":"free/public","licensing":"public-record terms; no full article storage","backtest":"suitable when acceptance time is retained","live":"potentially suitable","access_status":"confirmed offline from existing SEC store"},
 {"family":"issuer investor-relations releases","regions":"issuer-dependent/global","historical_depth":"inconsistent","publication_timestamps":"often present, must be timezone-aware","corrections":"inconsistent","issuer_identifiers":"usually weak; map to durable IDs","access":"feeds/pages/vendor APIs","request_limits":"site-specific","cost":"free to paid","licensing":"issuer/site terms; metadata only by default","backtest":"theoretical; archived versions required","live":"theoretical","access_status":"theoretical—not probed"},
 {"family":"exchange/regulatory announcement services","regions":"exchange-specific","historical_depth":"service-specific","publication_timestamps":"typically authoritative","corrections":"often explicit","issuer_identifiers":"exchange IDs/ISIN vary","access":"feeds/APIs/files","request_limits":"contract-specific","cost":"free to paid","licensing":"redistribution restrictions likely","backtest":"theoretical; potentially strong","live":"theoretical","access_status":"theoretical—not probed"},
 {"family":"official government contract and enforcement publications","regions":"agency/jurisdiction-specific","historical_depth":"agency-specific","publication_timestamps":"often dated; exact time varies","corrections":"agency-specific","issuer_identifiers":"contractor/entity IDs, often not securities","access":"APIs/bulk/official releases","request_limits":"agency-specific","cost":"generally free/public","licensing":"public-record terms; attribution required","backtest":"conditional on timestamp/entity mapping","live":"theoretical","access_status":"theoretical—not probed"},
 {"family":"licensed news APIs","regions":"vendor/plan-specific","historical_depth":"plan-specific","publication_timestamps":"generally supplied; semantics require validation","corrections":"vendor-specific","issuer_identifiers":"vendor-specific","access":"licensed API","request_limits":"contract/plan-specific","cost":"paid","licensing":"storage, display, derived-data restrictions","backtest":"theoretical; contract and timestamp audit required","live":"theoretical","access_status":"theoretical—not contracted or probed"},
 {"family":"open web/news aggregators","regions":"broad but uneven","historical_depth":"unstable/incomplete","publication_timestamps":"unreliable or updated-page only","corrections":"usually weak","issuer_identifiers":"usually absent","access":"feeds/search/pages","request_limits":"service/site-specific","cost":"free to paid","licensing":"copyright and terms constrain storage/reuse","backtest":"unsuitable unless provenance/version time proven","live":"context-only theoretical","access_status":"theoretical—not probed"},
 {"family":"Yahoo Finance","regions":"broad current coverage","historical_depth":"not established for point-in-time news","publication_timestamps":"not defensibly validated here","corrections":"not established","issuer_identifiers":"ticker-centric; reuse risk","access":"current pages/undocumented or licensed channels","request_limits":"not established","cost":"unknown/free-facing","licensing":"storage/reuse not established","backtest":"unsuitable until timestamps and license are proven","live":"latest/current context only","access_status":"theoretical—not probed"},
]


def _readonly(research: Path, production: Path, operation) -> dict[str, Any]:
    validate_paths(research, production)
    before = {str(p): fingerprint(p) for p in (research, production)}
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    with duckdb.connect(str(research), read_only=True) as db: payload = operation(db)
    after = {str(p): fingerprint(p) for p in (research, production)}
    if before != after: raise RuntimeError("read-only operation changed a database")
    return {**payload, "database_immutability":{"verified":True,"before":before,"after":after},
        "generated_rankings":0,"generated_candidates":0,"generated_shadow_selections":0,
        "notice":"CONTEXT ONLY — NOT USED IN SCORE", "recommendation":"NO NEWS-BASED RECOMMENDATION."}


def capability(research: Path, production: Path) -> dict[str, Any]:
    return _readonly(research, production, lambda _: {"command":"news-events-capability",
        "sources":SOURCE_CAPABILITIES,"categories":[x.value for x in EventCategory],
        "limits":{"summary_chars":MAX_SUMMARY_CHARS,"headline_chars":MAX_HEADLINE_CHARS,"event_rows":MAX_OUTPUT_EVENTS}})


def sec_readiness(research: Path, production: Path) -> dict[str, Any]:
    def query(db):
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if "sec_event_metadata" in tables:
            total, earliest, latest, amendments, with_items = db.execute(
                "SELECT count(*),min(public_at),max(public_at),sum(is_amendment),sum(item_codes<>'[]') FROM sec_event_metadata"
            ).fetchone()
            aggregate = lambda column: {str(key): int(count) for key, count in db.execute(
                f"SELECT {column},count(*) FROM sec_event_metadata GROUP BY {column} ORDER BY {column}"
            ).fetchall()}
            assessed = db.execute("SELECT count(*) FROM sec_event_checkpoints").fetchone()[0]
            return {"command":"sec-events-readiness","issuers_assessed":int(assessed),
                "filings":int(total),"event_filings":int(total),"amendments":int(amendments or 0),
                "forms":aggregate("form"),"publication_range":{"earliest":earliest,"latest":latest},
                "item_metadata_available":bool(with_items),
                "item_metadata_coverage":round(int(with_items or 0)/int(total),4) if total else 0.0,
                "event_categories":aggregate("event_category"),
                "coverage":aggregate("scope"),
                "affected_symbol_samples":[row[0][:80] for row in db.execute(
                    "SELECT DISTINCT qualified_symbol FROM sec_event_metadata ORDER BY 1 LIMIT 10").fetchall()],
                "classification":"bounded to SEC item metadata; reduced confidence when unavailable"}
        if "sec_filings" not in tables: return {"command":"sec-events-readiness","filings":0,"amendments":0,"publication_range":{"earliest":None,"latest":None},"categories":{}}
        columns = {r[1] for r in db.execute("PRAGMA table_info('sec_filings')").fetchall()}
        forms = db.execute("SELECT form,count(*),min(public_at),max(public_at) FROM sec_filings WHERE form IN ('8-K','8-K/A') GROUP BY form ORDER BY form").fetchall()
        return {"command":"sec-events-readiness","filings":sum(r[1] for r in forms),"amendments":sum(r[1] for r in forms if r[0]=='8-K/A'),
            "forms":{r[0]:r[1] for r in forms},"publication_range":{"earliest":min((r[2] for r in forms if r[2]),default=None),"latest":max((r[3] for r in forms if r[3]),default=None)},
            "item_metadata_available":"items" in columns,"classification":"bounded to SEC item metadata; general when unavailable"}
    return _readonly(research, production, query)


def status(research: Path, production: Path) -> dict[str, Any]:
    ready = sec_readiness(research, production)
    ready["command"] = "news-events-status"
    ready["confirmed_sources"] = 1
    ready["theoretical_sources"] = len(SOURCE_CAPABILITIES) - 1
    ready["stored_full_articles"] = 0
    return ready
