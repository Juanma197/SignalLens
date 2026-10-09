"""Index-fund prices for the scorecard's market benchmark.

Three ETFs stand in for "the market": SPY (S&P 500) is the headline comparison;
IJH (S&P MidCap 400) and IJR (S&P SmallCap 600) match the prototype's
300M-10B USD size band more closely. Prices go to the separate prototype store,
never the research database. One EODHD request per fund: ten years on the first
run, then the last few weeks (rewritten, as the provider may revise them).

    python -m app.prototype.benchmarks --prototype-db PATH   (token in SIGNALLENS_EODHD_API_TOKEN)
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path

from ..eodhd_ingestion import EODHDClient, EODHDLimits

FUNDS = {'SPY.US': 'S&P 500 (SPY)', 'IJH.US': 'S&P MidCap 400 (IJH)', 'IJR.US': 'S&P SmallCap 600 (IJR)'}
MARKET = 'SPY.US'
HISTORY_DAYS = 3653
OVERLAP_DAYS = 31


def parse(payload, symbol, retrieved_at):
    if not isinstance(payload, list): raise ValueError('PROTOTYPE_BENCHMARK_PAYLOAD_INVALID')
    rows = []
    for item in payload:
        try:
            day = date.fromisoformat(str(item['date'])[:10])
            close, adjusted = float(item['close']), float(item['adjusted_close'])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(close) and math.isfinite(adjusted) and close > 0 and adjusted > 0:
            rows.append((symbol, day, close, adjusted, retrieved_at, 'eodhd'))
    return rows


def fetch(store, client, *, today=None, now=None):
    """Download each fund from its last stored date (minus an overlap) to today."""
    today = today or datetime.now(timezone.utc).date()
    now = now or datetime.now(timezone.utc)
    latest = store.benchmark_latest_dates()
    report = {}
    for symbol in FUNDS:
        start = latest[symbol] - timedelta(days=OVERLAP_DAYS) if symbol in latest else today - timedelta(days=HISTORY_DAYS)
        try:
            rows = parse(client.get(f'eod/{symbol}', {'from': start.isoformat(), 'to': today.isoformat(), 'period': 'd'}), symbol, now)
            store.store_benchmark_prices(rows)
            report[symbol] = {'status': 'completed', 'from': start.isoformat(), 'rows': len(rows)}
        except Exception as exc:  # report and continue with the other funds
            report[symbol] = {'status': 'failed', 'from': start.isoformat(), 'error': type(exc).__name__}
    return {'command': 'benchmarks', 'funds': report, 'requests': client.requests}


def main(argv=None):
    from .store import PrototypeStore
    parser = argparse.ArgumentParser(description='Download index-fund prices for the prototype scorecard.')
    parser.add_argument('--prototype-db', required=True, type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get('SIGNALLENS_EODHD_API_TOKEN', '')
    if not token: parser.error('set SIGNALLENS_EODHD_API_TOKEN')
    client = EODHDClient(token, EODHDLimits(daily_requests=10, requests_per_minute=20, maximum_runtime_seconds=300))
    result = fetch(PrototypeStore(args.prototype_db), client)
    print(json.dumps(result, indent=2))
    return 0 if all(f['status'] == 'completed' for f in result['funds'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
