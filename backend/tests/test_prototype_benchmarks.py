"""Index-fund prices for the market benchmark: download, overlap, storage, scoring (offline)."""
from datetime import date, datetime, timezone

import pytest

from app.prototype.benchmarks import FUNDS, fetch, parse
from app.prototype.store import PrototypeStore

TODAY = date(2026, 10, 9)


class FakeClient:
    def __init__(self, fail=()):
        self.calls, self.requests, self.fail = [], 0, set(fail)

    def get(self, endpoint, params):
        self.calls.append((endpoint, params['from'])); self.requests += 1
        if endpoint.split('/')[1] in self.fail: raise RuntimeError('provider_request_failed')
        return [{'date': '2026-10-07', 'close': 100, 'adjusted_close': 99}, {'date': '2026-10-08', 'close': 101, 'adjusted_close': 100},
                {'date': 'bad'}, {'date': '2026-10-06', 'close': 0, 'adjusted_close': 0}]


def test_parse_skips_unusable_rows():
    rows = parse(FakeClient().get('eod/SPY.US', {'from': ''}), 'SPY.US', datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert [(r[1], r[3]) for r in rows] == [(date(2026, 10, 7), 99.0), (date(2026, 10, 8), 100.0)]
    with pytest.raises(ValueError): parse({'error': 'x'}, 'SPY.US', None)


def test_first_run_downloads_ten_years_then_only_an_overlap(tmp_path):
    store = PrototypeStore(tmp_path / 'p.duckdb')
    now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    client = FakeClient(fail={'IJR.US'})
    report = fetch(store, client, today=TODAY, now=now)
    assert [c[0] for c in client.calls] == [f'eod/{s}' for s in FUNDS] and client.calls[0][1] == '2016-10-08'
    assert report['funds']['SPY.US'] == {'status': 'completed', 'from': '2016-10-08', 'rows': 2}
    assert report['funds']['IJR.US']['status'] == 'failed'
    assert store.benchmark_prices(now) == {'SPY.US': {date(2026, 10, 7): 99.0, date(2026, 10, 8): 100.0},
                                           'IJH.US': {date(2026, 10, 7): 99.0, date(2026, 10, 8): 100.0},
                                           'VT.US': {date(2026, 10, 7): 99.0, date(2026, 10, 8): 100.0}}
    assert store.benchmark_prices(datetime(2026, 10, 9, tzinfo=timezone.utc)) == {}  # not yet retrieved then
    again = FakeClient()
    fetch(store, again, today=TODAY, now=now)
    assert dict(again.calls) == {'eod/SPY.US': '2026-09-07', 'eod/IJH.US': '2026-09-07', 'eod/IJR.US': '2016-10-08',
                                 'eod/VT.US': '2026-09-07'}
    assert len(store.benchmark_prices(now)['SPY.US']) == 2  # rewritten, not duplicated
