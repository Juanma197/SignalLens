"""Monthly cycle in the daily run: record the month, one review, one contribution reminder (offline)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.prototype import store as store_module
from app.prototype.alerts import contribution_reminder, deposited_in, monthly_review_message
from app.prototype.fixture import DECISION

OCT = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def test_review_message_lists_picks_counts_and_contribution_status():
    view = {'picks': [{'rank': 1, 'qualified_symbol': 'AAA.US', 'upside': 0.35, 'conviction': 'high', 'risk': 'medium', 'held': True}],
            'holdings': [{'decision': 'HOLD'}, {'decision': 'HOLD'}, {'decision': 'SELL'}],
            'allocation': {'account': {'cash_pool': 304.6}}}
    text = monthly_review_message(view, now=OCT, deposited=0, contribution=250, recorded=True, link='https://x/m')
    assert text.startswith('SignalLens monthly review: October 2026')
    assert '#1 AAA.US: upside +35% · conviction high · risk medium · already held' in text
    assert 'Your holdings: HOLD 2 · SELL 1' in text
    assert 'Cash pool £305; planned £250 not recorded yet.' in text
    assert 'recorded for the scorecard' in text and 'https://x/m' in text
    empty = monthly_review_message({'picks': [], 'holdings': [], 'allocation': {}}, now=OCT, deposited=250, contribution=250, recorded=False)
    assert 'No company qualifies' in empty and 'No holdings recorded yet.' in empty and '£250 deposited this month' in empty
    assert 'scorecard' not in empty
    assert "your £250 SignalLens contribution for October isn't recorded yet" in contribution_reminder(now=OCT, contribution=250)


def test_deposits_count_by_calendar_month_and_ignore_voids():
    moves = [{'kind': 'deposit', 'amount': 200, 'moved_on': '2026-10-02'}, {'kind': 'deposit', 'amount': 50, 'moved_on': '2026-09-30'},
             {'kind': 'withdrawal', 'amount': 20, 'moved_on': '2026-10-03'}, {'kind': 'deposit', 'amount': 99, 'moved_on': '2026-10-04', 'voided_at': 'x'}]
    assert deposited_in(moves, '2026-10') == 200 and deposited_in(moves, '2026-11') == 0


class FakeNotifier:
    def __init__(self): self.sent = []
    def send(self, text): self.sent.append(text)


@pytest.fixture
def store(prototype_fixture, tmp_path, monkeypatch):
    from app.config import Settings
    from app.prototype import api
    research, production = (Path(p) for p in prototype_fixture)
    settings = Settings(database_path=production, research_database_path=research, prototype_database_path=tmp_path / 'p' / 'p.duckdb',
                        staging_mode=True, api_token='t', prototype_writes_enabled=True)
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    api._CACHE.clear(); api._MARKET.clear()
    return store_module.PrototypeStore(settings.prototype_database_path, protected_paths=(research, production))


def test_daily_run_records_the_month_reviews_once_and_reminds_once(store):
    from app.prototype import alerts
    store.record_trade('buy', 'SYN05', 10, 1, '2026-09-01', account_amount=8)
    first = FakeNotifier()
    result = alerts.run(notifier=first, update=False, now=DECISION)
    assert result['steps']['monthly_cycle'] == {'record': 'recorded'}
    assert [r['month'] for r in store.decision_records()] == ['2026-10']
    reviews = [t for t in first.sent if t.startswith('SignalLens monthly review')]
    assert len(reviews) == 1 and 'planned £200 not recorded yet' in reviews[0]
    # Later in the month: no second review, no second record; a reminder only from the 8th.
    second = FakeNotifier()
    alerts.run(notifier=second, update=False, now=DECISION + timedelta(days=6))
    assert not any('monthly review' in t or 'Reminder' in t for t in second.sent)
    eighth = FakeNotifier()
    alerts.run(notifier=eighth, update=False, now=DECISION + timedelta(days=7))
    assert sum(t.startswith('Reminder: your £200') for t in eighth.sent) == 1
    ninth = FakeNotifier()
    alerts.run(notifier=ninth, update=False, now=DECISION + timedelta(days=8))
    assert not any(t.startswith('Reminder') for t in ninth.sent)


def test_no_reminder_once_the_contribution_is_recorded_or_when_none_is_planned(store):
    from app.prototype import alerts
    store.record_cash('deposit', 200, '2026-10-02', today=DECISION.date() + timedelta(days=7))
    late = DECISION + timedelta(days=7)
    sent = FakeNotifier()
    alerts.run(notifier=sent, update=False, now=late)
    assert not any(t.startswith('Reminder') for t in sent.sent)
    assert any('£200 deposited this month' in t for t in sent.sent)
    store.save_settings(monthly_contribution=0, max_holdings=10)
    printed = alerts.monthly_cycle(store, {'picks': [], 'holdings': [], 'allocation': {}}, notifier=None, now=late.replace(month=11), dry_run=True)
    assert not any(t.startswith('Reminder') for t in printed['messages'])
