"""Backtest phase 3: US companies that delisted since 2019, for the replay only.

docs/backtest-phase3.md. The replay's catalogue is today's listings, so companies
that failed, were taken over or left the exchange since 2019 are missing; for a
value strategy those are exactly the cheap companies that went wrong. This module
adds them to the research database, never to the live prototype (which only shows
listings in the active catalogue):

1. discover: EODHD's delisted US list (1 request), common stock on a main exchange.
   SEC's current ticker file no longer lists these tickers, so each company name is
   matched to SEC's list of every filer name (including former names; 1 download)
   and each candidate filer is verified from its filing history (1 SEC request per
   candidate): US periodic reports (10-K/10-Q) since 2017. Tickers now used by an
   active catalogue listing and filers already in the catalogue (ticker changes, not
   delistings) are excluded. Nothing large is downloaded yet: the report says how
   many EODHD requests the price stage needs.
2. prices: daily prices and dividends from EODHD (2 requests per company). Kept only
   when trading ended between 2019 and 30 days ago and the filing history overlaps
   the trading period.
3. sec: SEC facts through the existing fundamentals ingestion, with the matched CIK.

Every stage is resumable: each company's status is its checkpoint.

    python -m app.delisted discover --research-db R --production-db P
    python -m app.delisted prices   --research-db R --production-db P
    python -m app.delisted sec      --research-db R --production-db P
    python -m app.delisted status   --research-db R --production-db P
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

import duckdb
import httpx

from .active_catalogue import select_active_catalogue
from .eodhd_ingestion import TEN_YEARS_DAYS, BudgetStop, EODHDClient, EODHDLimits, _dividends, parse_catalogue, parse_eod
from .global_market_data import GlobalMarketDataRepository
from .global_universe import ListingObservation
from .model_readiness import fingerprint
from .sec_capability import SEC_SUBMISSIONS
from .sec_ingestion import AUTHORIZATION_PHRASE as SEC_AUTHORIZATION, BudgetClient, IngestionLimits, ingest as sec_ingest, valid_user_agent, validate_paths

SEC_NAMES = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
MAX_NAMES_BYTES = 400_000_000
MAPPING_SOURCE = "sec_name_match_delisted"
FIRST_SESSION_WANTED = date(2019, 1, 1)   # the replay's first month
PERIODIC_SINCE = date(2017, 1, 1)         # the method needs recent annual and quarterly figures
STILL_TRADING_DAYS = 30
FILINGS_BEFORE_DELISTING_DAYS = 548       # last 10-K/10-Q within 18 months of the last session

SCHEMA = """
CREATE TABLE IF NOT EXISTS delisted_listings(
  security_id VARCHAR PRIMARY KEY, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL,
  company_name VARCHAR NOT NULL, exchange VARCHAR, isin VARCHAR, cik VARCHAR, sec_name VARCHAR,
  candidate_ciks VARCHAR, status VARCHAR NOT NULL, reason VARCHAR, ticker_confirmed BOOLEAN,
  first_filing DATE, last_filing DATE, first_session DATE, last_session DATE,
  discovered_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
"""
# Statuses: candidate (name matched, filer not yet verified) -> mapped (one verified
# filer) -> priced (prices stored, trading ended in the window) -> ready (SEC facts
# stored). Anything else is an exclusion, with its reason.
IN_UNIVERSE = ('priced', 'ready')


def match_name(value: str) -> str:
    """Company name in the form both sources share: EDGAR's incorporation marks
    ("APPLE INC /CA/"), punctuation and legal-form words removed."""
    value = value.upper()
    while True:
        stripped = re.sub(r"\s*/[A-Z0-9]{1,4}/?\s*$", "", value)
        if stripped == value: break
        value = stripped
    value = re.sub(r"[^A-Z0-9 ]", " ", value.replace("&", " AND "))
    value = re.sub(r"\b(THE|INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LIMITED|LTD|LLC|LP|PLC)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def sec_name_index(text: str) -> dict[str, set[str]]:
    """{matching name: CIKs} from SEC's cik-lookup-data ("NAME:0000123456:" per line)."""
    index: dict[str, set[str]] = {}
    for line in text.splitlines():
        parts = line.rstrip().rsplit(":", 2)
        if len(parts) != 3 or not parts[1].isdigit(): continue
        key = match_name(parts[0])
        if key: index.setdefault(key, set()).add(parts[1].zfill(10))
    return index


def fetch_sec_names(user_agent: str, transport: httpx.BaseTransport | None = None) -> str:
    """SEC's list of every filer name (tens of MB of text; one request)."""
    if not valid_user_agent(user_agent): raise ValueError("valid contact-bearing SIGNALLENS_SEC_USER_AGENT required")
    chunks, size = [], 0
    with httpx.Client(transport=transport, headers={"User-Agent": user_agent}, timeout=120) as client:
        with client.stream("GET", SEC_NAMES) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_NAMES_BYTES: raise RuntimeError("oversized_response")
                chunks.append(chunk)
    return b"".join(chunks).decode("latin-1")


def filing_summary(submissions: dict[str, Any]) -> dict[str, Any]:
    """Periodic-report dates and the filer's current tickers, from a submissions document."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms, dates = recent.get("form") or [], recent.get("filingDate") or []
    periodic = sorted(date.fromisoformat(d) for f, d in zip(forms, dates) if str(f).startswith(("10-K", "10-Q")) and d)
    foreign = any(str(f).startswith(("20-F", "40-F")) for f in forms)
    return {"first": periodic[0] if periodic else None, "last": periodic[-1] if periodic else None,
            "foreign_only": foreign and not periodic, "tickers": {str(t).upper() for t in submissions.get("tickers") or []},
            "name": submissions.get("name")}


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _active(db) -> tuple[set[str], set[str]]:
    """Qualified symbols of the active catalogue and the CIKs mapped to it."""
    active = select_active_catalogue(db)
    if active is None: return set(), set()
    symbols = set(active.listings["qualified_symbol"].astype(str))
    ids = set(active.listings["security_id"].astype(str))
    tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
    ciks = {str(c) for s, c in db.execute("SELECT security_id, cik FROM sec_issuers").fetchall() if str(s) in ids} if "sec_issuers" in tables else set()
    return symbols, ciks


def _guard(research: Path, production: Path) -> dict:
    validate_paths(research, production)
    with duckdb.connect(str(production), read_only=True) as p: p.execute("SELECT 1")
    with duckdb.connect(str(research)) as db: db.execute(SCHEMA)
    return fingerprint(production)


def _rows(db, where: str = "", args: list | None = None) -> list[dict[str, Any]]:
    cursor = db.execute(f"SELECT * FROM delisted_listings {where} ORDER BY qualified_symbol", args or [])
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def _set(db, sid: str, now: datetime, **fields) -> None:
    fields["updated_at"] = now
    db.execute(f"UPDATE delisted_listings SET {', '.join(f'{k}=?' for k in fields)} WHERE security_id=?", [*fields.values(), sid])


def discover(*, research: Path, production: Path, eodhd: EODHDClient | None, sec: BudgetClient | None,
             names_text: Callable[[], str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Stage 1. With `eodhd` the delisted list is read and new candidates are stored;
    candidates are then verified against SEC (resumable: runs until done or the SEC
    request budget is spent)."""
    before, at = _guard(research, production), _now(now)
    added = excluded = 0
    if eodhd is not None:
        payload = eodhd.get("exchange-symbol-list/US", {"delisted": "1", "type": "common_stock"})
        accepted, filtered = parse_catalogue(payload, "US")
        index = sec_name_index(names_text()) if names_text else {}
        with duckdb.connect(str(research)) as db:
            symbols, active_ciks = _active(db)
            known = {r[0] for r in db.execute("SELECT qualified_symbol FROM delisted_listings").fetchall()}
            for item in accepted:
                symbol = item.qualified_symbol
                if symbol in known: continue
                ciks = sorted(index.get(match_name(item.company_name), set()))
                if symbol in symbols: state, reason = "excluded", "ticker_used_by_active_listing"
                elif not ciks: state, reason = "excluded", "sec_name_not_found"
                elif set(ciks) <= active_ciks: state, reason = "excluded", "filer_in_active_catalogue"
                else: state, reason = "candidate", None
                db.execute("INSERT INTO delisted_listings (security_id, qualified_symbol, ticker, company_name, exchange, isin, "
                           "candidate_ciks, status, reason, discovered_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           [f"delisted:{symbol}", symbol, item.ticker.upper(), item.company_name, (item.raw or {}).get("Exchange"),
                            item.isin, json.dumps([c for c in ciks if c not in active_ciks]), state, reason, at, at])
                added += state == "candidate"; excluded += state != "candidate"
        filtered_out = len(filtered)
    else:
        filtered_out = None
    verified, stop = 0, None
    if sec is not None:
        with duckdb.connect(str(research)) as db: candidates = _rows(db, "WHERE status='candidate'")
        for row in candidates:
            try: summaries = {cik: filing_summary(sec.get(SEC_SUBMISSIONS.format(cik=cik))) for cik in json.loads(row["candidate_ciks"])}
            except RuntimeError as exc:
                if str(exc) in {"request_budget_exhausted", "runtime_budget_exhausted"}: stop = str(exc); break
                with duckdb.connect(str(research)) as db: _set(db, row["security_id"], at, status="excluded", reason="sec_unavailable")
                continue
            except ValueError:
                with duckdb.connect(str(research)) as db: _set(db, row["security_id"], at, status="excluded", reason="sec_unavailable")
                continue
            usable = {c: s for c, s in summaries.items() if s["last"] and s["last"] >= PERIODIC_SINCE}
            confirmed = {c: s for c, s in usable.items() if row["ticker"] in s["tickers"]}
            chosen = confirmed if len(confirmed) == 1 else usable
            with duckdb.connect(str(research)) as db:
                if len(chosen) == 1:
                    cik, s = next(iter(chosen.items()))
                    _set(db, row["security_id"], at, status="mapped", cik=cik, sec_name=s["name"], ticker_confirmed=bool(confirmed),
                         first_filing=s["first"], last_filing=s["last"]); verified += 1
                else:
                    reason = ("filer_ambiguous" if chosen else "foreign_filer" if any(s["foreign_only"] for s in summaries.values())
                              else "no_periodic_reports_since_2017")
                    _set(db, row["security_id"], at, status="excluded", reason=reason)
    report = status(research=research, production=production)
    if fingerprint(production) != before: raise RuntimeError("production database changed")
    return {"command": "discover", "new_candidates": added, "new_exclusions": excluded, "filtered_by_type_or_venue": filtered_out,
            "verified": verified, "stop_reason": stop, "eodhd_requests_for_prices": 2 * report["counts"].get("mapped", 0)} | report


def prices(*, research: Path, production: Path, eodhd: EODHDClient, now: datetime | None = None) -> dict[str, Any]:
    """Stage 2: prices and dividends for mapped companies; resumable."""
    before, at = _guard(research, production), _now(now)
    start, end = at.date() - timedelta(days=TEN_YEARS_DAYS), at.date()
    market = GlobalMarketDataRepository(research)
    with duckdb.connect(str(research)) as db: targets = _rows(db, "WHERE status='mapped'")
    done, stop, failures = 0, None, 0
    for row in targets:
        first = last = None
        listing = ListingObservation(f"eodhd:{row['qualified_symbol']}", row["ticker"], "US", row["company_name"], "US", "USD",
                                     "common_stock", active=False, exchange_symbol=row["qualified_symbol"])
        try:
            rows = parse_eod(eodhd.get(f"eod/{row['qualified_symbol']}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"}),
                             listing, at, start, end)
            first, last = (rows[0].trading_date, rows[-1].trading_date) if rows else (None, None)
            if not rows or last < FIRST_SESSION_WANTED: outcome = ("excluded", "traded_before_2019_only")
            elif last > end - timedelta(days=STILL_TRADING_DAYS): outcome = ("excluded", "still_trading_or_ticker_reused")
            elif row["last_filing"] < last - timedelta(days=FILINGS_BEFORE_DELISTING_DAYS) or row["first_filing"] > last:
                outcome = ("excluded", "filings_do_not_match_trading")
            else:
                actions = _dividends(eodhd.get(f"div/{row['qualified_symbol']}", {"from": start.isoformat(), "to": end.isoformat()}), listing, at, start, end)
                market.store(rows, actions); outcome = ("priced", None)
        except BudgetStop as exc: stop = exc.reason; break
        except Exception: outcome = ("excluded", "price_download_failed"); failures += 1
        with duckdb.connect(str(research)) as db:
            _set(db, row["security_id"], at, status=outcome[0], reason=outcome[1], first_session=first if outcome[0] == "priced" else None,
                 last_session=last if outcome[0] == "priced" else None)
        done += 1
    report = status(research=research, production=production)
    if fingerprint(production) != before: raise RuntimeError("production database changed")
    return {"command": "prices", "processed": done, "failures": failures, "stop_reason": stop, "requests": eodhd.requests} | report


def sec(*, research: Path, production: Path, limits: IngestionLimits, now: datetime | None = None,
        transport: httpx.BaseTransport | None = None, fixture: dict | None = None) -> dict[str, Any]:
    """Stage 3: SEC facts for priced companies, through the fundamentals ingestion."""
    _guard(research, production); at = _now(now)
    with duckdb.connect(str(research)) as db: targets = _rows(db, "WHERE status='priced'")
    listings = [{"security_id": r["security_id"], "qualified_symbol": r["qualified_symbol"], "ticker": r["ticker"], "cik": r["cik"],
                 "issuer_name": r["sec_name"], "mapping_source": MAPPING_SOURCE} for r in targets]
    result = sec_ingest(research=research, production=production, authorization=SEC_AUTHORIZATION, dry_run=False, limits=limits,
                        listings=listings, now=at, transport=transport, fixture=fixture) if listings else {"selected": 0}
    with duckdb.connect(str(research)) as db:
        done = {r[0] for r in db.execute("SELECT security_id FROM sec_checkpoints WHERE status='completed'").fetchall()}
        for r in targets:
            if r["security_id"] in done: _set(db, r["security_id"], at, status="ready")
    return {"command": "sec", "ingestion": result} | status(research=research, production=production)


def status(*, research: Path, production: Path) -> dict[str, Any]:
    with duckdb.connect(str(research), read_only=True) as db:
        if "delisted_listings" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}:
            return {"counts": {}, "exclusions": {}, "last_sessions_by_year": {}}
        counts = dict(db.execute("SELECT status, count(*) FROM delisted_listings GROUP BY 1 ORDER BY 1").fetchall())
        reasons = dict(db.execute("SELECT reason, count(*) FROM delisted_listings WHERE status='excluded' GROUP BY 1 ORDER BY 2 DESC").fetchall())
        years = dict(db.execute("SELECT CAST(year(last_session) AS VARCHAR), count(*) FROM delisted_listings WHERE status IN ('priced','ready') GROUP BY 1 ORDER BY 1").fetchall())
        confirmed = db.execute("SELECT count(*) FROM delisted_listings WHERE status IN ('mapped','priced','ready') AND ticker_confirmed").fetchone()[0]
    return {"counts": counts, "exclusions": reasons, "last_sessions_by_year": years, "ticker_confirmed_by_sec": confirmed}


def universe_rows(db) -> list[dict[str, Any]]:
    """Delisted companies in the replay universe (priced or ready), for the materializer,
    SEC events and the replay. Empty when phase 3 has not run."""
    if "delisted_listings" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}: return []
    cursor = db.execute("SELECT security_id, qualified_symbol, ticker, company_name, cik, first_session, last_session "
                        "FROM delisted_listings WHERE status IN ('priced','ready') ORDER BY qualified_symbol")
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Backtest phase 3: delisted US companies (research only)")
    parser.add_argument("command", choices=["discover", "prices", "sec", "status"])
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--production-db", type=Path, required=True)
    parser.add_argument("--sec-max-requests", type=int, default=6000)
    parser.add_argument("--eodhd-daily-requests", type=int, default=12000)
    parser.add_argument("--requests-per-minute", type=int, default=60)
    parser.add_argument("--maximum-runtime-seconds", type=float, default=14400)
    parser.add_argument("--verify-only", action="store_true", help="discover: only continue verifying stored candidates against SEC")
    args = parser.parse_args(argv)
    try:
        if args.command == "status": result = status(research=args.research_db, production=args.production_db)
        else:
            token, agent = os.environ.get("SIGNALLENS_EODHD_API_TOKEN", ""), os.environ.get("SIGNALLENS_SEC_USER_AGENT", "")
            sec_limits = IngestionLimits(max_requests=args.sec_max_requests, runtime_seconds=args.maximum_runtime_seconds, max_response_bytes=10_000_000)
            eodhd = lambda: EODHDClient(token, EODHDLimits(daily_requests=args.eodhd_daily_requests, requests_per_minute=args.requests_per_minute,
                                                           maximum_runtime_seconds=args.maximum_runtime_seconds))
            if args.command == "discover":
                result = discover(research=args.research_db, production=args.production_db, eodhd=None if args.verify_only else eodhd(),
                                  sec=BudgetClient(agent, sec_limits), names_text=lambda: fetch_sec_names(agent))
            elif args.command == "prices": result = prices(research=args.research_db, production=args.production_db, eodhd=eodhd())
            else: result = sec(research=args.research_db, production=args.production_db, limits=sec_limits)
        print(json.dumps(result, sort_keys=True, default=str))
    except Exception as exc:
        message = re.sub(r"api_token=[^&\s'\"]+", "api_token=REDACTED", str(exc))[:300]  # provider URLs carry the token
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "message": message}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
