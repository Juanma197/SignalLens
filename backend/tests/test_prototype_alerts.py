"""Daily alerts: change detection, wording, delivery records, and the daily price update (offline)."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.prototype import store as store_module
from app.prototype.alerts import TelegramNotifier, change_message, plan_messages
from app.prototype.fixture import DECISION

FRIDAY = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)
RULES = {'maximum_position_weight_for_buying': 0.25}


def holding(symbol, decision, *, thesis='intact', upside=0.4, weight=0.1, value=1000.0, shares=10, reasons=('Because.',)):
    return {'qualified_symbol': symbol, 'decision': decision, 'reasons': list(reasons), 'shares': shares, 'market_value': value,
            'weight': weight, 'evidence': {'upside': upside}, 'checks': {'overall': thesis}}


def monthly(*holdings, picks=(), sales=()):
    return {'holdings': list(holdings), 'picks': list(picks), 'rules': RULES, 'decision_at': FRIDAY.isoformat(),
            'allocation': {'sales': list(sales)}}


def test_first_run_sends_one_baseline_then_only_changes():
    view = monthly(holding('AAA.US', 'HOLD'), holding('BBB.US', 'BUY MORE'),
                   picks=[{'rank': 1, 'qualified_symbol': 'LKQ.US', 'upside': 1.3, 'held': False}])
    first = plan_messages(view, {}, now=FRIDAY)
    assert [m[0] for m in first] == ['baseline'] * 3 and 'alerts are on' in first[0][4]
    # Only holdings are messaged; new opportunities stay on the page.
    assert 'LKQ' not in first[0][4] and 'AAA.US: HOLD' in first[0][4] and 'BBB.US: BUY MORE' in first[0][4]
    states = {'AAA.US': ('HOLD', 'intact'), 'BBB.US': ('BUY MORE', 'intact')}
    assert plan_messages(view, states, now=FRIDAY - timedelta(days=1)) == []  # Thursday, nothing changed
    changed = plan_messages(monthly(holding('AAA.US', 'SELL', thesis='broken', reasons=('The thesis is broken:', 'Net loss.')),
                                    holding('BBB.US', 'BUY MORE', thesis='warning')), states, now=FRIDAY - timedelta(days=1))
    assert [(m[1], m[2], m[3]) for m in changed] == [('AAA.US', 'SELL', 'broken'), ('BBB.US', 'BUY MORE', 'warning')]
    assert changed[0][4].startswith('AAA.US: HOLD → SELL\nSell all 10 shares (≈ $1,000).\n• The thesis is broken:\n• Net loss.')


def test_weekly_summary_once_on_fridays():
    view = monthly(holding('AAA.US', 'HOLD'))
    states = {'AAA.US': ('HOLD', 'intact')}
    assert [m[0] for m in plan_messages(view, states, now=FRIDAY)] == ['summary']
    assert plan_messages(view, states, now=FRIDAY, last_summary=FRIDAY - timedelta(hours=1)) == []
    assert [m[0] for m in plan_messages(view, states, now=FRIDAY, last_summary=FRIDAY - timedelta(days=7))] == ['summary']


def test_size_lines_for_reduce_and_buy_more():
    view = monthly(sales=[{'qualified_symbol': 'AAA.US', 'shares': 5, 'amount': 500.0}])
    reduce = change_message(holding('AAA.US', 'REDUCE', upside=-0.05), ('HOLD', 'intact'), view)
    assert 'Reduce by 5 shares (≈ $500, 50% of the position).' in reduce and 'Upside -5%' in reduce
    buy = change_message(holding('BBB.US', 'BUY MORE', weight=0.1, value=1000.0), ('HOLD', 'intact'), view, link='https://x/m')
    assert 'Room to add ≈ $1,500 before the 25% position limit.' in buy and buy.endswith('https://x/m')


def test_telegram_notifier_posts_and_raises_on_failure():
    seen = []
    def transport(request):
        seen.append(request)
        return httpx.Response(200, json={'ok': True}) if b'"ok"' in request.content else httpx.Response(400, json={'ok': False})
    notifier = TelegramNotifier('123:abc', '42', transport=httpx.MockTransport(transport))
    notifier.send('ok')
    assert seen[0].url.path == '/bot123:abc/sendMessage' and b'"chat_id":"42"' in seen[0].content.replace(b' ', b'')
    with pytest.raises(RuntimeError): notifier.send('fails')
    with pytest.raises(ValueError): TelegramNotifier('', '42')


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


class FakeNotifier:
    def __init__(self, fail=False): self.sent, self.fail = [], fail
    def send(self, text):
        if self.fail: raise RuntimeError('down')
        self.sent.append(text)


def test_run_records_what_was_delivered_and_retries_failures(paths, monkeypatch):
    from app.config import Settings
    from app.prototype import alerts, api
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='t', prototype_writes_enabled=True)
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    api._CACHE.clear(); api._MARKET.clear()
    store = store_module.PrototypeStore(paths[2], protected_paths=paths[:2])
    store.record_trade('buy', 'SYN05', 10, 1, '2026-09-01')
    down = FakeNotifier(fail=True)
    first = alerts.run(notifier=down, update=False, now=DECISION)
    assert first['messages'] == [] and store.alert_states() == {}  # nothing counted as sent
    up = FakeNotifier()
    alerts.run(notifier=up, update=False, now=DECISION)
    assert len(up.sent) == 2 and 'alerts are on' in up.sent[0] and 'SYN05.US: HOLD' in up.sent[0]  # above the limit: a review
    assert up.sent[1].startswith('SignalLens monthly review: October 2026')  # first run of the month; the failed one is retried
    assert store.alert_states() == {'SYN05.US': ('HOLD', 'unknown')}
    again = FakeNotifier()
    alerts.run(notifier=again, update=False, now=DECISION)
    assert again.sent == []  # Thursday 1 Oct: no change, no summary
    printed = alerts.run(dry_run=True, update=False, now=DECISION)
    assert printed['messages'] == [] and len(store.alert_events()) == 4


def test_daily_prices_fill_every_missing_weekday_then_mark_coverage(paths):
    from app.daily_prices import covered_through, update
    from app.eodhd_ingestion import EODHDClient, EODHDLimits
    research, production = paths[:2]
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS eodhd_ingestion_checkpoints (stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP, PRIMARY KEY(stage, qualified_symbol))")
        symbols = [r[0] for r in db.execute("SELECT qualified_symbol FROM security_listings").fetchall()]
        db.executemany("INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES ('refresh', ?, 'completed', NULL, TIMESTAMP '2026-10-01 23:00:00')", [[s] for s in symbols])
    calls = []
    def transport(request):
        day = request.url.params['date']; calls.append((day, request.url.params.get('type')))
        if request.url.params.get('type') == 'splits': return httpx.Response(200, json=[])
        if request.url.params.get('type') == 'dividends':
            return httpx.Response(200, json=[{'code': 'SYN01', 'date': day, 'dividend': '0.25'}] if day == '2026-10-05' else [])
        return httpx.Response(200, json=[{'code': 'SYN01', 'date': day, 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'adjusted_close': 10.5, 'volume': 100},
                                         {'code': 'NOTLISTED', 'date': day, 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'adjusted_close': 1, 'volume': 1},
                                         {'code': 'SYN02', 'date': day, 'open': 10, 'high': 9, 'low': 11, 'close': 10, 'adjusted_close': 10, 'volume': 1}])
    client = EODHDClient('secret', EODHDLimits(daily_requests=50, requests_per_minute=100000), transport=httpx.MockTransport(transport), sleep=lambda _: None)
    report = update(research=research, production=production, client=client, now=datetime(2026, 10, 6, 23, tzinfo=timezone.utc))
    assert report['status'] == 'completed' and [d['date'] for d in report['days']] == ['2026-10-02', '2026-10-05', '2026-10-06']
    assert report['days'][1] == {'date': '2026-10-05', 'prices': 1, 'dividends': 1, 'splits': 0, 'rebased': []}  # invalid SYN02 row and unlisted code skipped
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM eodhd_ingestion_checkpoints WHERE stage = 'bulk_daily'").fetchone()[0] == len(symbols)
        assert db.execute("SELECT value FROM global_corporate_actions WHERE qualified_symbol = 'SYN01.US' AND ex_date = DATE '2026-10-05'").fetchone()[0] == 0.25
        assert covered_through(db, symbols) == date(2026, 10, 6)
    # Nothing new before the next close: no requests.
    assert update(research=research, production=production, client=client, now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc))['days'] == []



def test_daily_prices_split_downloads_the_listing_again_on_one_basis(paths):
    from app.daily_prices import update
    from app.eodhd_ingestion import EODHDClient, EODHDLimits
    research, production = paths[:2]
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS eodhd_ingestion_checkpoints (stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP, PRIMARY KEY(stage, qualified_symbol))")
        symbols = [r[0] for r in db.execute("SELECT qualified_symbol FROM security_listings").fetchall()]
        db.executemany("INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES ('refresh', ?, 'completed', NULL, TIMESTAMP '2026-10-02 23:00:00')", [[s] for s in symbols])
        db.execute("UPDATE global_price_observations SET source = 'eodhd'")  # only provider rows are rebased
        first = db.execute("SELECT min(trading_date) FROM global_price_observations WHERE qualified_symbol = 'SYN01.US'").fetchone()[0]
    histories = []
    def transport(request):
        if request.url.path.endswith('/eod/SYN01.US'):
            histories.append(request.url.params['from'])
            return httpx.Response(200, json=[{'date': d, 'open': 5, 'high': 6, 'low': 4, 'close': 5.25, 'adjusted_close': 5.25, 'volume': 100}
                                             for d in (request.url.params['from'], '2026-10-05')])
        day, kind = request.url.params['date'], request.url.params.get('type')
        if kind == 'splits':
            return httpx.Response(200, json=[{'code': 'SYN01', 'exchange': 'US', 'date': day, 'split': '2.000000/1.000000'}] if day == '2026-10-05' else [])
        if kind == 'dividends': return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{'code': 'SYN01', 'date': day, 'open': 5, 'high': 6, 'low': 4, 'close': 5.25, 'adjusted_close': 5.25, 'volume': 100}])
    client = EODHDClient('secret', EODHDLimits(daily_requests=50, requests_per_minute=100000), transport=httpx.MockTransport(transport), sleep=lambda _: None)
    report = update(research=research, production=production, client=client, now=datetime(2026, 10, 5, 23, tzinfo=timezone.utc))
    assert report['status'] == 'completed' and report['days'][0]['rebased'] == ['SYN01.US']
    assert histories == [first.isoformat()]  # everything stored is fetched again, not just ten years
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("SELECT value FROM global_corporate_actions WHERE qualified_symbol = 'SYN01.US' AND action_type = 'split'").fetchone()[0] == 2
        assert db.execute("SELECT close, retrieved_at FROM global_price_observations WHERE qualified_symbol = 'SYN01.US' AND trading_date = ?", [first]).fetchone() == (5.25, datetime(2026, 10, 5, 23))


def test_daily_prices_failed_split_rebase_stops_before_storing_the_day(paths):
    from app.daily_prices import update
    from app.eodhd_ingestion import EODHDClient, EODHDLimits
    research, production = paths[:2]
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS eodhd_ingestion_checkpoints (stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP, PRIMARY KEY(stage, qualified_symbol))")
        symbols = [r[0] for r in db.execute("SELECT qualified_symbol FROM security_listings").fetchall()]
        db.executemany("INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES ('refresh', ?, 'completed', NULL, TIMESTAMP '2026-10-02 23:00:00')", [[s] for s in symbols])
        db.execute("UPDATE global_price_observations SET source = 'eodhd'")  # only provider rows are rebased
    def transport(request):
        if '/eod/' in request.url.path: return httpx.Response(200, json=[{'date': 'not-a-date'}])
        if request.url.params.get('type') == 'splits':
            return httpx.Response(200, json=[{'code': 'SYN01', 'date': request.url.params['date'], 'split': '1.000000/10.000000'}])
        return httpx.Response(200, json=[])
    client = EODHDClient('secret', EODHDLimits(daily_requests=50, requests_per_minute=100000), transport=httpx.MockTransport(transport), sleep=lambda _: None)
    report = update(research=research, production=production, client=client, now=datetime(2026, 10, 5, 23, tzinfo=timezone.utc))
    assert report['status'] == 'stopped' and report['stop_reason'] == 'split_rebase_failed' and report['days'] == []
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM global_corporate_actions WHERE action_type = 'split'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM eodhd_bulk_days").fetchone()[0] == 0

def test_telegram_chat_id_lists_who_messaged_the_bot():
    from app.prototype.alerts import telegram_chat_id
    def transport(request):
        assert request.url.path == '/bot123:abc/getUpdates'
        return httpx.Response(200, json={'ok': True, 'result': [{'message': {'chat': {'id': 42}}}, {'message': {'chat': {'id': 42}}}, {'edited_message': {}}]})
    assert telegram_chat_id('123:abc', transport=httpx.MockTransport(transport)) == [42]
    with pytest.raises(RuntimeError):
        telegram_chat_id('123:abc', transport=httpx.MockTransport(lambda r: httpx.Response(401, json={'ok': False})))
    with pytest.raises(ValueError): telegram_chat_id('')
