"""Backtest phase 2 measurement on synthetic markets with known answers (offline)."""
from datetime import date, datetime, timedelta, timezone
import json

import duckdb
import pytest

from app.model_readiness import fingerprint
from app.prototype.measure import (GLOBAL, SP500, Market, Series, block_bootstrap_mean, buckets, criteria, index_plan,
                                   performance, pick_returns, pick_summary, report, simulate, summary)
from app.prototype.replay import month_cutoffs

START = date(2019, 1, 1)
GROWTH = {'A.US': 0.02, 'B.US': 0.0, 'C.US': -0.01, 'D.US': 0.0, GLOBAL: 0.01, SP500: 0.005}   # per month
STATUS = {'A.US': 'candidate', 'B.US': 'watch', 'C.US': 'value_trap', 'D.US': 'not_undervalued'}
RATE = 0.8


def weekdays(first, last):
    d, out = first, []
    while d <= last:
        if d.weekday() < 5: out.append(d)
        d += timedelta(days=1)
    return out


DAYS = weekdays(START, date(2021, 3, 31))


def price(symbol, d):
    return 100 * (1 + GROWTH[symbol]) ** ((d - START).days / (365.25 / 12))


def market():
    series = {s: Series([(d, price(s, d)) for d in DAYS]) for s in GROWTH}
    return Market({s: v for s, v in series.items() if s not in (GLOBAL, SP500)}, Series([(d, RATE) for d in DAYS]),
                  {GLOBAL: series[GLOBAL], SP500: series[SP500]})


def months(count=18):
    out = []
    for cutoff in month_cutoffs(START, date(2019 + (count - 1) // 12, (count - 1) % 12 + 1, 1)):
        eligible = [{'security_id': s.lower(), 'qualified_symbol': s, 'thesis': 'intact', 'signs': []} for s in STATUS]
        assessments = [{'security_id': s.lower(), 'qualified_symbol': s, 'status': STATUS[s], 'upside': 0.4 if s == 'A.US' else 0.05,
                        'conviction': 'high', 'risk': 'low', 'score': 0.3 if s == 'A.US' else 0.0} for s in STATUS]
        out.append({'cutoff': cutoff.date(), 'eligible': eligible, 'assessments': assessments, 'picks': ['a.us']})
    return out


def test_series_lookups_use_the_next_session_and_refuse_gaps():
    s = Series([(date(2020, 1, 2), 10), (date(2020, 1, 3), 11), (date(2020, 2, 20), 12)])
    assert s.after(date(2020, 1, 2)) == (date(2020, 1, 3), 11.0)
    assert s.after(date(2020, 1, 3)) is None                       # next session 48 days away
    assert s.on_or_before(date(2020, 1, 10)) == (date(2020, 1, 3), 11.0)
    assert s.on_or_before(date(2020, 2, 10), max_gap=7) is None and s.on_or_before(date(2019, 12, 1)) is None


def test_picks_are_measured_in_pounds_against_the_eligible_average_and_random_picks():
    m, ms = market(), months()
    rows = pick_returns(ms, m, 12)
    assert len(rows) == 6                                           # 18 months, 12 ahead
    assert rows[0]['picks'][0] == pytest.approx(1.02 ** 12 - 1, abs=0.01)
    assert rows[0]['global'] == pytest.approx(1.01 ** 12 - 1, abs=0.01)
    result = pick_summary(rows, 12, draws=200)
    skill = result['skill_vs_eligible']
    assert skill['mean'] > 0.2 and skill['low'] > 0 and result['random_pick_percentile'] == 1.0
    assert result['pick_hit_rate_vs_eligible'] == 1.0 and result['months_beating_global'] == 1.0
    excess = buckets(ms, rows)
    assert excess['candidate']['average_excess'] > 0 > excess['value_trap']['average_excess']
    # A picker of the worst company shows negative skill.
    worst = [dict(x, picks=['c.us']) for x in ms]
    assert pick_summary(pick_returns(worst, m, 12), 12, draws=200)['skill_vs_eligible']['high'] < 0


def test_block_bootstrap_interval_and_performance_arithmetic():
    flat = block_bootstrap_mean([0.1] * 24, block=12, draws=100)
    assert flat['low'] == pytest.approx(0.1) and flat['high'] == pytest.approx(0.1)
    assert block_bootstrap_mean([], block=3)['mean'] is None
    still = performance([{'value_gbp': 200.0 * (i + 1)} for i in range(24)])
    assert still['money_weighted_annual'] == pytest.approx(0, abs=1e-6) and still['time_weighted_annual'] == pytest.approx(0)
    assert still['max_drawdown'] == 0 and still['deposited_gbp'] == 4800
    value, path = 0.0, []
    for _ in range(24):
        value = value * 1.01 + 200
        path.append({'value_gbp': value})
    grown = performance(path)
    assert grown['money_weighted_annual'] == pytest.approx(1.01 ** 12 - 1, abs=1e-6)
    assert grown['time_weighted_annual'] == pytest.approx(1.01 ** 12 - 1, abs=1e-9)


def test_the_policy_simulation_follows_the_live_rules_with_costs():
    m, ms = market(), months()
    sim = simulate(ms, m)
    result = performance(sim['path'])
    assert len(sim['path']) == 18 and result['deposited_gbp'] == 3600
    assert sim['trades']['buys'] > 0 and sim['trades']['costs_gbp'] > 0 and sim['trades']['forced_exits'] == 0
    assert all(p['holdings'] <= 1 for p in sim['path'])          # only A is ever picked
    # A grows 2% a month, but the 25% limit leaves most money as cash: the policy beats cash, trails nothing silly.
    assert result['final_value_gbp'] > 3600 and sim['path'][-1]['cash_gbp'] > 0
    index = performance(index_plan(ms, m, GLOBAL)['path'])
    assert index['money_weighted_annual'] == pytest.approx(1.01 ** 12 - 1, abs=0.01)
    verdict = criteria(sim, index_plan(ms, m, GLOBAL), {'low': 0.1, 'high': 0.3}, holdout=False)
    assert not verdict['decisive'] and len(verdict['not_decisive_because']) == 2 and verdict['verdict'].endswith('(not decisive)')
    assert criteria(sim, None, None, holdout=True, survivors_only=False)['verdict'] == 'insufficient data'


def test_report_reads_databases_read_only_and_stores_the_result(tmp_path):
    research, prototype, replay = tmp_path / 'research.duckdb', tmp_path / 'prototype.duckdb', tmp_path / 'replay.duckdb'
    with duckdb.connect(str(research)) as db:
        db.execute("""CREATE TABLE global_price_observations(qualified_symbol VARCHAR, trading_date DATE, exchange VARCHAR, status VARCHAR,
                      adjusted_close DOUBLE)""")
        db.executemany("INSERT INTO global_price_observations VALUES (?, ?, 'US', 'available', ?)",
                       [[s, d, price(s, d)] for s in STATUS for d in DAYS])
        db.execute('CREATE TABLE global_fx_observations(base_currency VARCHAR, quote_currency VARCHAR, observed_on DATE, rate DOUBLE)')
        db.executemany("INSERT INTO global_fx_observations VALUES ('USD', 'GBP', ?, ?)", [[d, RATE] for d in DAYS])
    with duckdb.connect(str(prototype)) as db:
        db.execute('CREATE TABLE benchmark_prices(qualified_symbol VARCHAR, trading_date DATE, adjusted_close DOUBLE)')
        db.executemany('INSERT INTO benchmark_prices VALUES (?, ?, ?)', [[s, d, price(s, d)] for s in (GLOBAL, SP500) for d in DAYS])
    from app.prototype.replay import SCHEMA
    with duckdb.connect(str(replay)) as db:
        db.execute(SCHEMA)
        db.execute("INSERT INTO replay_runs VALUES ('r1', now(), now(), 'completed', now(), '2019-01', '2020-06', false, 'v', 'h', 's', NULL)")
        for month in months():
            cutoff = datetime.combine(month['cutoff'], datetime.min.time().replace(hour=23, minute=59), timezone.utc)
            body = {k: v for k, v in month.items() if k != 'cutoff'}
            db.execute("INSERT INTO replay_months VALUES ('r1', ?, 'completed', NULL, 4, 1, 'x', ?)", [cutoff, json.dumps(body)])
    before = fingerprint(research)
    result = report(replay, research, prototype, draws=100)
    assert fingerprint(research) == before and result['run_id'] == 'r1' and not result['holdout']
    assert result['months'] == 18 and result['picks']['12m']['months_with_picks'] == 6 and 'warnings' not in result
    assert result['global_index']['performance']['final_value_gbp'] > 3600
    assert 'path' not in summary(result)['policy'] and len(result['policy']['path']) == 18
    with duckdb.connect(str(replay), read_only=True) as db:
        assert db.execute('SELECT count(*) FROM replay_reports').fetchone()[0] == 1
