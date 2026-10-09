"""Backtest phase 1 replay: public dates instead of retrieval, no leakage, holdout guard (offline)."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import duckdb
import pytest

from app.model_readiness import fingerprint
from app.prototype.fixture import DECISION
from app.prototype.replay import (HOLDOUT_START, ReplayError, _check_months, _encode, month_cutoffs, run, runs, split_check)
from app.prototype.service import _build, _evidence, _share_counts

LATER = DECISION + timedelta(days=5)          # the replay's knowledge horizon ("now")
MID_SEPTEMBER = datetime(2026, 9, 15, 23, 59, tzinfo=timezone.utc)


@pytest.fixture
def research(prototype_fixture):
    return Path(prototype_fixture[0]), Path(prototype_fixture[1])


def build(path, decision, known=None):
    with duckdb.connect(str(path), read_only=True) as db:
        return json.loads(_encode(_build(db, decision, 15, known=known)))


def test_known_equal_to_decision_is_the_live_result(research):
    assert build(research[0], DECISION) == build(research[0], DECISION, known=DECISION)


def test_replay_counts_data_from_when_it_was_public_not_when_it_was_retrieved(research):
    # Every fixture row was retrieved on 30 September: live, mid-September sees nothing.
    live = build(research[0], MID_SEPTEMBER)
    assert live['eligible_count'] == 0 and live['blockers'] == ['completed_active_catalogue_unavailable']
    replay = build(research[0], MID_SEPTEMBER, known=LATER)
    calendar = replay['session_calendar']
    assert calendar['derived_sessions'] > 0 and calendar['last_session'] <= '2026-09-15'
    assert replay['knowledge_horizon'].startswith(LATER.date().isoformat())
    # No leakage: nothing after the cutoff is used. Too few sessions for the window, and
    # the 10-Q fact published on 30 September is not visible yet.
    for c in replay['companies']:
        assert not c['eligible'] and 'insufficient_visible_exchange_sessions' in c['reasons']
        assert all(e['public_at'] <= MID_SEPTEMBER.isoformat() for e in c['direct_evidence'])
        assert any('evidence_after_cutoff' in m['reasons'] for m in c['missing_data'])
    # The share count filed on 28 July was public by mid-September: visible in replay only.
    with duckdb.connect(str(research[0]), read_only=True) as db:
        assert _share_counts(db, MID_SEPTEMBER) == {}
        replayed = _share_counts(db, MID_SEPTEMBER, LATER, cache={})
        assert replayed and all(e['filed'] == date(2026, 7, 28) for rows in replayed.values() for e in rows)
        assert _share_counts(db, datetime(2026, 7, 28, 12, tzinfo=timezone.utc), LATER) == {}  # filed that day: not yet public


def test_replay_at_the_live_cutoff_matches_live_eligibility(research):
    live, replay = build(research[0], DECISION), build(research[0], DECISION, known=LATER)
    assert replay['eligible_count'] == live['eligible_count'] > 0
    assert replay['value_ranking']['picks'] == live['value_ranking']['picks']


def test_facts_need_a_public_date_by_the_decision_and_a_retrieval_by_the_horizon():
    sec = {'security_id': 's', 'qualified_symbol': 'AAA.US', 'cik': '0000000001'}
    def fact(public, retrieved):
        return {'security_id': 's', 'qualified_symbol': 'AAA.US', 'cik': '1', 'concept': 'CashAndCashEquivalentsAtCarryingValue',
                'taxonomy': 'us-gaap', 'unit': 'USD', 'currency': 'USD', 'value': 5.0, 'period_end': date(2020, 3, 31), 'period_start': None,
                'form': '10-Q', 'accession_number': '0000000001-20-000001', 'fact_key': 'k', 'public_at': public, 'retrieved_at': retrieved,
                'source_endpoint': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json'}
    decision = datetime(2020, 6, 1, tzinfo=timezone.utc)
    filed, retrieved = datetime(2020, 5, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
    data = {'sec_facts': [fact(filed, retrieved)]}
    assert _evidence(sec, data, decision)[0] == []                                  # live: retrieved too late
    assert len(_evidence(sec, data, decision, known=LATER)[0]) == 1                 # replay: public in time
    late = {'sec_facts': [fact(datetime(2020, 6, 2, tzinfo=timezone.utc), retrieved)]}
    cash = next(m for m in _evidence(sec, late, decision, known=LATER)[1] if m['field'] == 'cash_and_cash_equivalents')
    assert cash['reasons'] == ['evidence_after_cutoff']                              # filed after the decision


def test_month_cutoffs_and_the_preregistered_ranges():
    cutoffs = month_cutoffs(date(2019, 1, 1), date(2022, 12, 1))
    assert len(cutoffs) == 48 and cutoffs[0] == datetime(2019, 1, 1, 23, 59, tzinfo=timezone.utc)
    assert datetime(2019, 6, 3, 23, 59, tzinfo=timezone.utc) in cutoffs  # 1 June 2019 was a Saturday
    _check_months(date(2019, 1, 1), date(2022, 12, 1), False)
    _check_months(HOLDOUT_START, date(2026, 9, 1), True)
    for first, last, holdout, code in [(date(2018, 12, 1), date(2019, 6, 1), False, 'REPLAY_BEFORE_FIRST_MONTH'),
                                       (date(2019, 1, 1), date(2023, 1, 1), False, 'REPLAY_HOLDOUT_NOT_DECLARED'),
                                       (date(2022, 12, 1), date(2023, 6, 1), True, 'REPLAY_HOLDOUT_MIXED_WITH_TUNING'),
                                       (date(2020, 5, 1), date(2020, 4, 1), False, 'REPLAY_EMPTY_RANGE')]:
        with pytest.raises(ReplayError, match=code): _check_months(first, last, holdout)


def test_run_stores_compact_months_leaves_databases_unchanged_and_holds_out_once(research, tmp_path):
    out = tmp_path / 'replay' / 'replay.duckdb'
    before = [fingerprint(p) for p in research]
    args = dict(research_db=research[0], production_db=research[1], replay_db=out, first=date(2026, 9, 1), last=date(2026, 10, 1),
                holdout=True, known_at=LATER)
    seen = []
    result = run(**args, progress=seen.append)
    assert result['months'] == 2 and result['completed'] == 2 and [fingerprint(p) for p in research] == before
    assert [m['decision_at'][:10] for m in seen] == ['2026-09-01', '2026-10-01']
    (stored,) = runs(out)
    assert stored['status'] == 'completed' and stored['holdout'] and stored['months'] == 2
    with duckdb.connect(str(out), read_only=True) as db:
        months = {d.astimezone(timezone.utc).date().isoformat(): j and json.loads(j)
                  for d, j in db.execute('SELECT decision_at, month_json FROM replay_months').fetchall()}
    october = months['2026-10-01']
    assert october['eligible_count'] == len(october['eligible']) > 0 and october['population'] == october['eligible_count']
    first = october['eligible'][0]
    assert {'security_id', 'qualified_symbol', 'close', 'market_cap_usd', 'thesis', 'warnings'} <= set(first)
    assert months['2026-09-01']['eligible_count'] == 0      # not enough sessions before September in the fixture
    with pytest.raises(ReplayError, match='REPLAY_HOLDOUT_ALREADY_RUN'): run(**args)
    with pytest.raises(ReplayError, match='REPLAY_DB_NOT_SEPARATE'): run(**(args | {'replay_db': research[0], 'holdout': False,
                                                                                    'first': date(2019, 1, 1), 'last': date(2019, 1, 1)}))
    with pytest.raises(ReplayError, match='REPLAY_FUTURE_CUTOFF'): run(**(args | {'known_at': datetime(2026, 9, 20, tzinfo=timezone.utc)}))


def test_split_check_finds_close_jumps_that_the_adjusted_close_absorbs(research):
    clean = split_check(research[0])
    assert clean['split_like_close_jumps'] == 0 and clean['us_symbols'] == 18
    with duckdb.connect(str(research[0])) as db:
        # A 2-for-1 split on SYN00 as an unadjusted provider would store it.
        db.execute("UPDATE global_price_observations SET close = close / 2, open = open / 2, high = high / 2, low = low / 2 "
                   "WHERE qualified_symbol = 'SYN00.US' AND trading_date >= DATE '2026-09-01'")
    found = split_check(research[0])
    assert found['split_like_close_jumps'] == 1 and found['examples'][0]['symbol'] == 'SYN00.US'
    assert found['examples'][0]['close_ratio'] == pytest.approx(0.5, abs=0.01)
