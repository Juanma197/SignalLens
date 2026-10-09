"""Backtest phase 2: measure a stored replay run (docs/backtest-preregistration.md).

Everything is in pounds and total return: US adjusted closes (dividends and
splits) converted at the stored GBP-per-USD rate of each session. A decision
made at a month's cutoff is executed at the next session's close.

1. Picks: forward 1-, 3-, 6- and 12-month returns of each month's Top 3 against
   the equal-weight average of that month's eligible companies (the expected
   result of picking at random), 1,000 random-pick strategies from the same
   companies, a VALL-like global index (VT) and the S&P 500 (SPY). The skill
   statistic is the monthly difference against the eligible average, with a
   90% interval from a moving-block bootstrap over months (overlapping
   horizons are serially correlated).
2. Ranking buckets: forward returns of each ranking status (candidate, watch,
   not undervalued, value trap, not assessable) against the eligible average.
3. The actual policy, simulated: a monthly deposit, the cash pool, the live
   decision and allocation rules (at most 10 holdings, 25% limit, fractional
   shares), Trading 212 costs (0.15% currency conversion each way plus an
   estimated spread). Compared with the same deposits into the global index
   (bought in pounds, no conversion fee).

Survivors only until phase 3, so no result here is decisive.

    python -m app.prototype.measure --replay-db PATH [--run-id ID]
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import random
import sys
import uuid

import duckdb

from ..model_readiness import fingerprint
from .allocation import allocate
from .decisions import decide
from .service import PrototypeError

HORIZONS = (1, 3, 6, 12)
DRAWS = 1000
SEED = 20261009
CONFIDENCE = 0.90
GLOBAL, SP500 = 'VT.US', 'SPY.US'
COSTS = {'fx': 0.0015, 'spread': 0.001}
POLICY = {'deposit_gbp': 200.0, 'max_holdings': 10, 'fractional': True}
MAX_ENTRY_GAP_DAYS = 10
SCHEMA = """CREATE TABLE IF NOT EXISTS replay_reports(
  report_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL, report_json VARCHAR NOT NULL);"""


# Series ------------------------------------------------------------------------
class Series:
    """Sorted (date, value) pairs with as-of and next-session lookups."""
    def __init__(self, rows):
        rows = sorted((d, float(v)) for d, v in rows if v is not None and math.isfinite(float(v)) and float(v) > 0)
        self.dates, self.values = [d for d, _ in rows], [v for _, v in rows]

    def on_or_before(self, day, max_gap=None):
        i = bisect_right(self.dates, day) - 1
        if i < 0 or (max_gap is not None and (day - self.dates[i]).days > max_gap): return None
        return self.dates[i], self.values[i]

    def after(self, day, max_gap=MAX_ENTRY_GAP_DAYS):
        i = bisect_right(self.dates, day)
        if i >= len(self.dates) or (self.dates[i] - day).days > max_gap: return None
        return self.dates[i], self.values[i]


class Market:
    """US adjusted closes by symbol, GBP-per-USD rates and index funds, all as Series."""
    def __init__(self, prices, fx, funds):
        self.prices, self.fx, self.funds = prices, fx, funds

    def gbp(self, series, day):
        """(session, value in pounds) of the first session after `day`, or None."""
        hit = series.after(day) if series else None
        rate = hit and self.fx.on_or_before(hit[0], max_gap=7)
        return (hit[0], hit[1] * rate[1]) if hit and rate else None

    def gbp_return(self, series, start, end):
        a, b = self.gbp(series, start), self.gbp(series, end)
        return b[1] / a[1] - 1 if a and b else None


# 1. Picks ---------------------------------------------------------------------
def _quantile(values, q):
    values = sorted(values)
    if not values: return None
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def block_bootstrap_mean(values, block, draws=DRAWS, seed=SEED):
    """Mean and 90% interval of `values` (one per month) by a circular moving-block bootstrap."""
    n = len(values)
    if n == 0: return {'mean': None, 'low': None, 'high': None, 'months': 0}
    rng, block = random.Random(seed), max(1, min(block, n))
    means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample += [values[(start + j) % n] for j in range(block)]
        means.append(sum(sample[:n]) / n)
    tail = (1 - CONFIDENCE) / 2
    return {'mean': sum(values) / n, 'low': _quantile(means, tail), 'high': _quantile(means, 1 - tail), 'months': n}


def pick_returns(months, market, horizon):
    """Per month with a later month `horizon` ahead: pick, eligible and fund returns."""
    rows = []
    for i, month in enumerate(months[:len(months) - horizon] if horizon else months):
        start, end = month['cutoff'], months[i + horizon]['cutoff']
        returns = {}
        for e in month['eligible']:
            r = market.gbp_return(market.prices.get(e['qualified_symbol']), start, end)
            if r is not None: returns[e['security_id']] = r
        if not returns: continue
        picks = [returns[s] for s in month['picks'] if s in returns]
        rows.append({'cutoff': start.isoformat(), 'eligible': returns, 'eligible_mean': sum(returns.values()) / len(returns),
                     'picks': picks, 'picks_missing': len(month['picks']) - len(picks),
                     'global': market.gbp_return(market.funds.get(GLOBAL), start, end),
                     'sp500': market.gbp_return(market.funds.get(SP500), start, end)})
    return rows


def pick_summary(rows, horizon, draws=DRAWS, seed=SEED):
    with_picks = [r for r in rows if r['picks']]
    if not with_picks: return {'horizon_months': horizon, 'months': len(rows), 'months_with_picks': 0}
    pick_means = [sum(r['picks']) / len(r['picks']) for r in with_picks]
    differences = [m - r['eligible_mean'] for m, r in zip(pick_means, with_picks)]
    # Random-pick strategies: each month, as many random eligible companies as there were picks.
    rng, strategies = random.Random(seed), []
    for _ in range(draws):
        means = [sum(rng.sample(list(r['eligible'].values()), len(r['picks']))) / len(r['picks']) for r in with_picks]
        strategies.append(sum(means) / len(means))
    actual = sum(pick_means) / len(pick_means)
    def fund(key): return [r[key] for r in with_picks if r[key] is not None]
    def beat(key): return [m > r[key] for m, r in zip(pick_means, with_picks) if r[key] is not None]
    return {'horizon_months': horizon, 'months': len(rows), 'months_with_picks': len(with_picks),
            'picks': sum(len(r['picks']) for r in with_picks), 'picks_without_price': sum(r['picks_missing'] for r in with_picks),
            'average_pick_return': actual, 'average_eligible_return': sum(r['eligible_mean'] for r in with_picks) / len(with_picks),
            'average_global_return': _mean(fund('global')), 'average_sp500_return': _mean(fund('sp500')),
            'skill_vs_eligible': block_bootstrap_mean(differences, block=max(1, horizon), draws=draws, seed=seed),
            'random_pick_percentile': sum(s < actual for s in strategies) / len(strategies),
            'random_pick_range': {'low': _quantile(strategies, 0.05), 'median': _quantile(strategies, 0.5), 'high': _quantile(strategies, 0.95)},
            'pick_hit_rate_vs_eligible': _mean([p > r['eligible_mean'] for r in with_picks for p in r['picks']]),
            'months_beating_global': _mean(beat('global')), 'months_beating_sp500': _mean(beat('sp500'))}


def _mean(values):
    values = [float(v) for v in values]
    return sum(values) / len(values) if values else None


# 2. Ranking buckets -----------------------------------------------------------
def buckets(months, rows):
    """Average excess return over the eligible average by ranking status (same horizon as `rows`)."""
    by_cutoff = {r['cutoff']: r for r in rows}
    out = {}
    for month in months:
        r = by_cutoff.get(month['cutoff'].isoformat())
        if not r: continue
        for a in month['assessments']:
            ret = r['eligible'].get(a['security_id'])
            if ret is None: continue
            out.setdefault(a['status'], []).append(ret - r['eligible_mean'])
    return {status: {'observations': len(v), 'average_excess': _mean(v)} for status, v in sorted(out.items())}


# 3. Policy simulation ------------------------------------------------------------
def _checks(entry):
    """The live decision input for an eligible company: its automatic signs (no personal conditions)."""
    return {'overall': entry['thesis'], 'checks': [], 'automatic': entry.get('signs', [])}


def simulate(months, market, *, policy=POLICY, costs=COSTS):
    """Run the live monthly rules on simulated money. Each month: deposit, value, decide,
    allocate, then execute at the next session (sales first). Returns values at each cutoff."""
    cash, positions, path, trades = 0.0, {}, [], {'buys': 0, 'sells': 0, 'traded_gbp': 0.0, 'costs_gbp': 0.0, 'forced_exits': 0}
    for month in months:
        cutoff = month['cutoff']
        rate = market.fx.on_or_before(cutoff, max_gap=7)
        if not rate: continue
        cash += policy['deposit_gbp']
        by_id = {e['security_id']: e for e in month['eligible']}
        assessments = {a['security_id']: a for a in month['assessments']}
        # Value holdings at the cutoff in dollars (adjusted-close units).
        holdings = []
        for sid, p in list(positions.items()):
            seen = market.prices.get(p['symbol']) and market.prices[p['symbol']].on_or_before(cutoff, max_gap=31)
            if not seen:  # no price for a month: leave at the last price (rare with survivors)
                last = market.prices.get(p['symbol']) and market.prices[p['symbol']].on_or_before(cutoff)
                if last:
                    cash += _sell_value(p['units'] * last[1], rate[1], costs, trades)
                trades['forced_exits'] += 1; del positions[sid]; continue
            holdings.append({'qualified_symbol': p['symbol'], 'security_id': sid, 'company_name': None, 'currency': 'USD',
                             'shares': p['units'], 'price': {'close': seen[1]}, 'market_value': p['units'] * seen[1]})
        total = sum(h['market_value'] for h in holdings)
        for h in holdings:
            h['weight'] = h['market_value'] / total if total else None
            entry = by_id.get(h['security_id'])
            h |= decide(h, assessments.get(h['security_id']), _checks(entry) if entry else None)
        picks = []
        for sid in month['picks']:
            entry, seen = by_id.get(sid), None
            if entry: seen = market.prices.get(entry['qualified_symbol']) and market.prices[entry['qualified_symbol']].on_or_before(cutoff, max_gap=7)
            if seen: picks.append({'company_name': None} | assessments[sid] | {'held': sid in positions, 'price': seen[1]})
        plan = allocate(holdings, picks, cash / rate[1], reinvest=True, max_holdings=policy['max_holdings'], fractional=policy['fractional'])
        for s in plan['sales']:
            p = positions[s['security_id']]
            hit = market.prices[p['symbol']].after(cutoff)
            fx = hit and market.fx.on_or_before(hit[0], max_gap=7)
            if not (hit and fx): continue
            units = min(p['units'], s['shares'])
            cash += _sell_value(units * hit[1], fx[1], costs, trades)
            p['units'] -= units
            if p['units'] <= 1e-9: del positions[s['security_id']]
        for b in plan['buys']:
            held = positions.get(b['security_id'])
            symbol = held['symbol'] if held else by_id[b['security_id']]['qualified_symbol']
            hit = market.prices[symbol].after(cutoff)
            fx = hit and market.fx.on_or_before(hit[0], max_gap=7)
            if not (hit and fx): continue
            cost_per_usd = fx[1] * (1 + costs['fx']) * (1 + costs['spread'])
            usd = min(b['amount'], cash / cost_per_usd)  # prices moved overnight: never spend more than the cash
            if usd <= 0: continue
            cash -= usd * cost_per_usd
            trades['buys'] += 1; trades['traded_gbp'] += usd * fx[1]; trades['costs_gbp'] += usd * (cost_per_usd - fx[1])
            p = positions.setdefault(b['security_id'], {'symbol': symbol, 'units': 0.0})
            p['units'] += usd / hit[1]
        path.append({'cutoff': cutoff.isoformat(), 'cash_gbp': cash, 'holdings': len(positions),
                     'value_gbp': cash + _value_gbp(positions, market, cutoff, rate[1])})
    return {'path': path, 'trades': trades}


def _sell_value(usd, rate, costs, trades):
    gross = usd * rate
    net = gross * (1 - costs['fx']) * (1 - costs['spread'])
    trades['sells'] += 1; trades['traded_gbp'] += gross; trades['costs_gbp'] += gross - net
    return net


def _value_gbp(positions, market, day, rate):
    total = 0.0
    for p in positions.values():
        seen = market.prices[p['symbol']].on_or_before(day)
        if seen: total += p['units'] * seen[1] * rate
    return total


def index_plan(months, market, fund, deposit=POLICY['deposit_gbp']):
    """The same deposits into an index fund bought in pounds at the next session (no conversion fee)."""
    units, path, series = 0.0, [], market.funds.get(fund)
    if not series: return None
    for month in months:
        cutoff = month['cutoff']
        rate = market.fx.on_or_before(cutoff, max_gap=7)
        hit = market.gbp(series, cutoff)
        if not (rate and hit): continue
        units += deposit / hit[1]
        seen = series.on_or_before(cutoff)
        path.append({'cutoff': cutoff.isoformat(), 'value_gbp': units * seen[1] * rate[1] if seen else None})
    return {'path': path}


def performance(path, deposit=POLICY['deposit_gbp']):
    """Money-weighted (annual IRR), time-weighted annualised return, volatility and drawdown.

    The value at each cutoff is measured just after that month's deposit, so the
    month's return is (value now - deposit) / value last month."""
    values = [p['value_gbp'] for p in path if p['value_gbp'] is not None]
    if len(values) < 2: return None
    monthly = [(v - deposit) / prev - 1 for prev, v in zip(values, values[1:]) if prev > 0]
    index, peak, drawdown = 1.0, 1.0, 0.0
    for r in monthly:
        index *= 1 + r; peak = max(peak, index); drawdown = min(drawdown, index / peak - 1)
    months = len(monthly)
    mean = sum(monthly) / months
    sd = math.sqrt(sum((r - mean) ** 2 for r in monthly) / (months - 1)) if months > 1 else 0.0
    deposited = deposit * len(values)
    return {'months': len(values), 'deposited_gbp': deposited, 'final_value_gbp': values[-1], 'gain_gbp': values[-1] - deposited,
            'money_weighted_annual': _irr(deposit, len(values), values[-1]),
            'time_weighted_annual': index ** (12 / months) - 1, 'volatility_annual': sd * math.sqrt(12), 'max_drawdown': drawdown}


def _irr(deposit, count, final):
    """Annual rate r such that monthly deposits (at the start of each of `count` months) grow to `final`."""
    def future(m):
        return sum(deposit * (1 + m) ** (count - 1 - i) for i in range(count))
    lo, hi = -0.99, 1.0
    if not future(lo) <= final <= future(hi): return None
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if future(mid) < final else (lo, mid)
    return (1 + (lo + hi) / 2) ** 12 - 1


# Report -----------------------------------------------------------------------
def criteria(simulation, global_plan, skill_12m, *, holdout, survivors_only=True):
    """The pre-registered pass criteria, with whether this run can decide anything."""
    sim, bench = performance(simulation['path']), global_plan and performance(global_plan['path'])
    portfolio = None if not (sim and bench) else ('pass' if sim['final_value_gbp'] > bench['final_value_gbp'] else 'fail')
    low, high = (skill_12m or {}).get('low'), (skill_12m or {}).get('high')
    skill = None if low is None else ('pass' if low > 0 else 'fail' if high < 0 else 'inconclusive')
    reasons = []
    if not holdout: reasons.append('tuning window (2019-2022), not the holdout')
    if survivors_only: reasons.append('survivors only: delisted companies are added in phase 3')
    return {'portfolio_beats_global_index': portfolio, 'picks_beat_random_with_interval_above_zero': skill,
            'decisive': not reasons, 'not_decisive_because': reasons,
            'verdict': ('adds value' if portfolio == 'pass' and skill == 'pass' else
                        'not shown' if None not in (portfolio, skill) else 'insufficient data') + ('' if not reasons else ' (not decisive)')}


def measure(months, market, *, holdout, draws=DRAWS):
    rows = {h: pick_returns(months, market, h) for h in HORIZONS}
    picks = {f'{h}m': pick_summary(rows[h], h, draws=draws) for h in HORIZONS}
    simulation = simulate(months, market)
    global_plan, sp500_plan = index_plan(months, market, GLOBAL), index_plan(months, market, SP500)
    return {'months': len(months), 'first': months[0]['cutoff'].isoformat() if months else None,
            'last': months[-1]['cutoff'].isoformat() if months else None,
            'picks': picks, 'ranking_buckets_12m': buckets(months, rows[12]), 'ranking_buckets_3m': buckets(months, rows[3]),
            'policy': {'rules': POLICY, 'costs': COSTS, 'performance': performance(simulation['path']), 'trades': simulation['trades'],
                       'average_cash_share': _mean([p['cash_gbp'] / p['value_gbp'] for p in simulation['path'] if p['value_gbp']]),
                       'average_holdings': _mean([p['holdings'] for p in simulation['path']]), 'path': simulation['path']},
            'global_index': {'fund': GLOBAL, 'performance': global_plan and performance(global_plan['path'])},
            'sp500': {'fund': SP500, 'performance': sp500_plan and performance(sp500_plan['path'])},
            'criteria': criteria(simulation, global_plan, picks['12m'].get('skill_vs_eligible'), holdout=holdout),
            'method': __doc__.split('\n\n')[1].strip()}


# Loading ----------------------------------------------------------------------
def load_months(replay_db, run_id=None):
    with duckdb.connect(str(replay_db), read_only=True) as db:
        if run_id is None:
            found = db.execute("SELECT run_id FROM replay_runs WHERE status = 'completed' ORDER BY created_at DESC LIMIT 1").fetchone()
            if not found: raise PrototypeError('MEASURE_NO_COMPLETED_RUN')
            run_id = found[0]
        run = db.execute('SELECT holdout, status FROM replay_runs WHERE run_id = ?', [run_id]).fetchone()
        if not run: raise PrototypeError('MEASURE_UNKNOWN_RUN')
        rows = db.execute("SELECT decision_at, month_json FROM replay_months WHERE run_id = ? AND status = 'completed' ORDER BY decision_at",
                          [run_id]).fetchall()
    months = []
    for decision, payload in rows:
        month = json.loads(payload)
        month['cutoff'] = decision.astimezone(timezone.utc).date()
        months.append(month)
    return run_id, bool(run[0]), months


def load_market(research_db, prototype_db, symbols, first, last):
    path = Path(research_db)
    before = fingerprint(path)
    symbols = sorted(set(symbols))
    with duckdb.connect(str(path), read_only=True, config={'memory_limit': '2GB', 'threads': 2, 'enable_external_access': False}) as db:
        rows = db.execute("""SELECT qualified_symbol, trading_date, adjusted_close FROM global_price_observations
            WHERE exchange = 'US' AND status = 'available' AND trading_date BETWEEN ? AND ?
              AND list_contains(?, qualified_symbol)""", [first, last, symbols]).fetchall()
        fx = db.execute("""SELECT observed_on, rate FROM global_fx_observations WHERE base_currency = 'USD' AND quote_currency = 'GBP'
                           AND observed_on BETWEEN ? AND ?""", [first, last]).fetchall() \
            if db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'global_fx_observations'").fetchone()[0] else []
    if fingerprint(path) != before: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    grouped = {}
    for s, d, v in rows: grouped.setdefault(s, []).append((d, v))
    prices = {s: Series(v) for s, v in grouped.items()}
    funds = {}
    if prototype_db and Path(prototype_db).is_file():
        with duckdb.connect(str(prototype_db), read_only=True) as db:
            if db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'benchmark_prices'").fetchone()[0]:
                for s, d, v in db.execute('SELECT qualified_symbol, trading_date, adjusted_close FROM benchmark_prices WHERE trading_date BETWEEN ? AND ?',
                                          [first, last]).fetchall():
                    funds.setdefault(s, []).append((d, v))
    return Market(prices, Series(fx), {s: Series(v) for s, v in funds.items()})


def report(replay_db, research_db, prototype_db, run_id=None, draws=DRAWS):
    run_id, holdout, months = load_months(replay_db, run_id)
    if not months: raise PrototypeError('MEASURE_NO_MONTHS')
    symbols = {e['qualified_symbol'] for m in months for e in m['eligible']}
    first = months[0]['cutoff']
    last = date(months[-1]['cutoff'].year + 2, 1, 1)
    market = load_market(research_db, prototype_db, symbols, first, last)
    result = measure(months, market, holdout=holdout, draws=draws) | {'run_id': run_id, 'holdout': holdout}
    for missing in (GLOBAL, SP500):
        if missing not in market.funds: result.setdefault('warnings', []).append(f'{missing} prices are not stored: run app.prototype.benchmarks first.')
    if not market.fx.dates: result.setdefault('warnings', []).append('No GBP/USD rates are stored for this period.')
    with duckdb.connect(str(replay_db)) as db:
        db.execute(SCHEMA)
        db.execute('INSERT INTO replay_reports VALUES (?, ?, ?, ?)', [uuid.uuid4().hex, run_id, datetime.now(timezone.utc),
                                                                      json.dumps(result, default=str, allow_nan=False)])
    return result


def summary(result):
    """The headline numbers, without the monthly path."""
    keep = dict(result)
    keep['policy'] = {k: v for k, v in result['policy'].items() if k != 'path'}
    return keep


def main(argv=None):
    from ..config import get_settings
    parser = argparse.ArgumentParser(description='Backtest phase 2: measure a stored replay run.')
    parser.add_argument('--replay-db', required=True, type=Path)
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        result = report(args.replay_db, settings.research_database_path, settings.prototype_database_path, args.run_id)
    except PrototypeError as exc:
        print(json.dumps({'status': 'failed', 'error': exc.code}), file=sys.stderr)
        return 1
    print(json.dumps(summary(result), indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
