"""Cash reassessment after a sale or deposit, and daily: message only a new, worthwhile use (offline)."""
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.prototype import store as store_module
from app.prototype.alerts import cash_message
from app.prototype.fixture import DECISION


def view(available, *buys, skipped=()):
    return {'decision_at': DECISION.isoformat(), 'allocation': {
        'buys': [{'action': a, 'qualified_symbol': s, 'shares': sh, 'price': p, 'amount_gbp': g, 'why': 'Because.'} for a, s, sh, p, g in buys],
        'skipped_no_slot': [{'qualified_symbol': s} for s in skipped],
        'account': {'available': available}}}


def test_cash_message_lists_buys_from_cash_actually_held():
    fingerprint, text = cash_message(view(554.6, ('NEW BUY', 'AAA.US', 0.125, 2000, 197.5), ('BUY MORE', 'BBB.US', 1, 120, 94.8),
                                          skipped=['CCC.US']), 'sale', symbol='OLD.US', link='https://x/monthly')
    assert text.startswith('Cash to put to work (after your sale of OLD.US): £555 available.')
    assert '• NEW BUY AAA.US: £198 (0.125 shares at ~$2,000.00). Because.' in text
    assert 'Leaves £262 as cash.' in text and 'No free place for CCC.US' in text
    assert 'https://x/monthly' in text and 'nothing has been traded' in text
    # Same money (in steps of 10) and the same names give the same fingerprint, whatever the order or small moves.
    again, _ = cash_message(view(556, ('BUY MORE', 'BBB.US', 1, 121, 95), ('NEW BUY', 'AAA.US', 0.1, 2100, 190)), 'daily')
    assert again == fingerprint
    assert cash_message(view(754.6, ('NEW BUY', 'AAA.US', 0.2, 2000, 300)), 'deposit')[0] != fingerprint
    assert cash_message(view(554.6), 'deposit') is None   # nothing worthwhile: silence
    assert cash_message({'allocation': {}}, 'daily') is None


class FakeNotifier:
    def __init__(self, fail=False): self.sent, self.fail = [], fail
    def send(self, text):
        if self.fail: raise RuntimeError('down')
        self.sent.append(text)


@pytest.fixture
def setup(prototype_fixture, tmp_path, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    research, production = (Path(p) for p in prototype_fixture)
    settings = Settings(database_path=production, research_database_path=research,
                        prototype_database_path=tmp_path / 'prototype' / 'p.duckdb',
                        staging_mode=True, api_token='t', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    api._CACHE.clear(); api._MARKET.clear()
    return settings, store_module.PrototypeStore(settings.prototype_database_path, protected_paths=(research, production))


def test_reassess_sends_once_and_retries_after_a_failure(setup, monkeypatch):
    from app.prototype import alerts, api
    _, store = setup
    current = {'view': view(300, ('NEW BUY', 'AAA.US', 1, 200, 160))}
    monkeypatch.setattr(api, 'monthly', lambda *a, **k: current['view'])
    down = FakeNotifier(fail=True)
    assert alerts.reassess('deposit', notifier=down, now=DECISION)['status'] == 'send_failed'
    up = FakeNotifier()
    assert alerts.reassess('deposit', notifier=up, now=DECISION)['status'] == 'sent' and len(up.sent) == 1
    assert alerts.reassess('daily', notifier=up, now=DECISION)['status'] == 'unchanged' and len(up.sent) == 1
    current['view'] = view(500, ('NEW BUY', 'AAA.US', 1, 200, 160), ('NEW BUY', 'BBB.US', 1, 100, 80))
    assert alerts.reassess('sale', symbol='OLD.US', notifier=up, now=DECISION)['status'] == 'sent'
    assert 'after your sale of OLD.US' in up.sent[-1]
    current['view'] = view(20)
    assert alerts.reassess('daily', notifier=up, now=DECISION)['status'] == 'nothing_worthwhile'
    assert sorted(e['delivered'] for e in store.reallocation_events()) == [False, True, True]  # one failure, two sent
    assert alerts.reassess('daily', dry_run=True, now=DECISION) == {'status': 'nothing_worthwhile'}


def test_sale_and_deposit_schedule_a_reassessment_only_with_telegram(setup, monkeypatch):
    from app import main
    from app.prototype import alerts
    calls = []
    monkeypatch.setattr(alerts, 'reassess', lambda trigger, **k: calls.append((trigger, k.get('symbol'))) or {'status': 'sent'})
    headers, root = {'Authorization': 'Bearer t'}, '/api/v1/research/prototype/store/portfolio'
    buy = {'kind': 'buy', 'qualified_symbol': 'SYN05', 'shares': 10, 'price': 1, 'traded_on': '2026-09-01', 'account_amount': 8}
    deposit = {'kind': 'deposit', 'amount': 200, 'moved_on': '2026-09-01'}
    with TestClient(main.app) as client:
        monkeypatch.delenv('SIGNALLENS_TELEGRAM_BOT_TOKEN', raising=False)
        assert client.post(f'{root}/cash', headers=headers, json=deposit).json()['reassessment'] == 'telegram_not_configured'
        monkeypatch.setenv('SIGNALLENS_TELEGRAM_BOT_TOKEN', 'x'); monkeypatch.setenv('SIGNALLENS_TELEGRAM_CHAT_ID', '1')
        bought = client.post(f'{root}/trades', headers=headers, json=buy)
        assert bought.status_code == 200 and 'reassessment' not in bought.json(), bought.text  # a buy spends cash
        sold = client.post(f'{root}/trades', headers=headers, json=buy | {'kind': 'sell', 'shares': 4, 'account_amount': 3.5, 'traded_on': '2026-09-02'}).json()
        assert sold.get('reassessment') == 'scheduled', sold
        assert client.post(f'{root}/cash', headers=headers, json=deposit).json()['reassessment'] == 'scheduled'
        assert 'reassessment' not in client.post(f'{root}/cash', headers=headers, json=deposit | {'kind': 'withdrawal', 'amount': 1}).json()
    assert calls == [('sale', 'SYN05.US'), ('deposit', None)]
