"""Market-cap pre-screen for widening the US catalogue.

The catalogue command only knows tickers and names, so it cannot tell a 300M
company from a 300B one. This step estimates every US listing's size from five
requests, so that only companies near the research size band get ten years of
prices and SEC filings downloaded:

* SEC company_tickers.json maps tickers to CIKs (1 request);
* SEC XBRL frames give each filer's cover-page shares outstanding for the last
  three calendar quarters (3 requests; the newest value per CIK is used);
* EODHD eod-bulk-last-day/US gives the latest close for every US ticker
  (1 request).

Estimated market cap = shares x close. It is a selection aid only: the
prototype computes its own point-in-time market cap later. Companies whose
cover page reports only share classes (no undimensioned total) have no
estimate and are reported, not guessed. Writes one run to us_size_prescreen in
the research database; reads nothing from and never writes to production.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import uuid

import duckdb

from .eodhd_ingestion import EODHDClient, EODHDLimits
from .model_readiness import fingerprint
from .sec_capability import SEC_TICKERS
from .sec_ingestion import BudgetClient, IngestionLimits, validate_paths

FRAME_URL = 'https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{year}Q{quarter}I.json'
BULK_ENDPOINT = 'eod-bulk-last-day/US'
QUARTERS = 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS us_size_prescreen(
  run_id VARCHAR NOT NULL, observed_at TIMESTAMPTZ NOT NULL, ticker VARCHAR NOT NULL, cik VARCHAR NOT NULL,
  shares DOUBLE NOT NULL, shares_period_end DATE NOT NULL, close DOUBLE NOT NULL, close_date DATE NOT NULL,
  market_cap_usd DOUBLE NOT NULL, PRIMARY KEY (run_id, ticker));
"""


def recent_quarters(today: date, count: int = QUARTERS) -> list[tuple[int, int]]:
    """The last `count` calendar quarters that have ended before `today`, newest first."""
    year, quarter = today.year, (today.month - 1) // 3  # the current quarter has not ended
    out = []
    while len(out) < count:
        if quarter == 0: year, quarter = year - 1, 4
        out.append((year, quarter)); quarter -= 1
    return out


def shares_by_cik(frames: list[dict]) -> dict[str, tuple[float, date]]:
    """Newest positive cover-page share count per CIK across the frames."""
    out: dict[str, tuple[float, date]] = {}
    for frame in frames:
        for row in frame.get('data', []) if isinstance(frame, dict) else []:
            try:
                cik, value, end = str(int(row['cik'])).zfill(10), float(row['val']), date.fromisoformat(str(row['end']))
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0 and (cik not in out or end > out[cik][1]):
                out[cik] = (value, end)
    return out


def ciks_by_ticker(payload: dict) -> dict[str, str]:
    out = {}
    for row in payload.values() if isinstance(payload, dict) else []:
        if isinstance(row, dict) and isinstance(row.get('cik_str'), int) and row.get('ticker'):
            out[str(row['ticker']).strip().upper()] = str(row['cik_str']).zfill(10)
    return out


def closes_by_ticker(payload: list) -> dict[str, tuple[float, date]]:
    out = {}
    for row in payload if isinstance(payload, list) else []:
        try:
            ticker, close, day = str(row['code']).strip().upper(), float(row['close']), date.fromisoformat(str(row['date'])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(close) and close > 0: out[ticker] = (close, day)
    return out


def estimates(tickers: dict[str, str], shares: dict[str, tuple[float, date]], closes: dict[str, tuple[float, date]]) -> list[dict]:
    rows = []
    for ticker, (close, close_day) in sorted(closes.items()):
        cik = tickers.get(ticker)
        if cik is None or cik not in shares: continue
        count, end = shares[cik]
        rows.append({'ticker': ticker, 'cik': cik, 'shares': count, 'shares_period_end': end,
                     'close': close, 'close_date': close_day, 'market_cap_usd': count * close})
    return rows


def run(*, research: Path, production: Path, eodhd: EODHDClient, sec: BudgetClient, now: datetime | None = None) -> dict:
    validate_paths(research, production)
    production_before = fingerprint(production)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    frames, missing = [], []
    for year, quarter in recent_quarters(now.date()):
        try: frames.append(sec.get(FRAME_URL.format(year=year, quarter=quarter)))
        except ValueError: missing.append(f'CY{year}Q{quarter}I')  # frame not published yet
    tickers = ciks_by_ticker(sec.get(SEC_TICKERS))
    closes = closes_by_ticker(eodhd.get(BULK_ENDPOINT))
    shares = shares_by_cik(frames)
    rows = estimates(tickers, shares, closes)
    run_id = uuid.uuid4().hex
    with duckdb.connect(str(research)) as db:
        db.execute(SCHEMA)
        db.executemany('INSERT INTO us_size_prescreen VALUES (?,?,?,?,?,?,?,?,?)',
                       [[run_id, now, r['ticker'], r['cik'], r['shares'], r['shares_period_end'], r['close'], r['close_date'], r['market_cap_usd']] for r in rows])
    if fingerprint(production) != production_before: raise RuntimeError('production database changed')
    bands = {'under_300m': 0, '300m_to_10b': 0, 'over_10b': 0}
    for r in rows:
        bands['under_300m' if r['market_cap_usd'] < 3e8 else 'over_10b' if r['market_cap_usd'] > 1e10 else '300m_to_10b'] += 1
    return {'command': 'us-size-prescreen', 'run_id': run_id, 'observed_at': now.isoformat(), 'estimates': len(rows),
            'by_size': bands, 'tickers_priced': len(closes), 'tickers_mapped_to_cik': len(tickers),
            'ciks_with_shares': len(shares), 'frames_missing': missing,
            'sec_requests': sec.count, 'eodhd_requests': eodhd.requests, 'production_unchanged': True}


def latest(db: duckdb.DuckDBPyConnection) -> dict[str, float] | None:
    """{ticker: estimated market cap} from the newest run, or None if none exists."""
    if not db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'us_size_prescreen'").fetchone()[0]:
        return None
    row = db.execute('SELECT run_id FROM us_size_prescreen ORDER BY observed_at DESC, run_id DESC LIMIT 1').fetchone()
    if row is None: return None
    return dict(db.execute('SELECT ticker, market_cap_usd FROM us_size_prescreen WHERE run_id = ?', [row[0]]).fetchall())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Estimate US listings\' market caps before widening the catalogue.')
    parser.add_argument('--research-db', type=Path, required=True)
    parser.add_argument('--production-db', type=Path, required=True)
    args = parser.parse_args(argv)
    token, agent = os.environ.get('SIGNALLENS_EODHD_API_TOKEN', ''), os.environ.get('SIGNALLENS_SEC_USER_AGENT', '')
    if not token or not agent: parser.error('set SIGNALLENS_EODHD_API_TOKEN and SIGNALLENS_SEC_USER_AGENT')
    try:
        result = run(research=args.research_db, production=args.production_db,
                     eodhd=EODHDClient(token, EODHDLimits(daily_requests=5, maximum_runtime_seconds=600, max_response_bytes=16 * 1024 * 1024)),
                     sec=BudgetClient(agent, IngestionLimits(max_requests=10, runtime_seconds=600, max_response_bytes=10_000_000)))
    except Exception as exc:  # provider URLs carry the token; never print exception text
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
