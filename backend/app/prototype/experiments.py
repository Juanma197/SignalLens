"""Tuning experiments: other ways to choose the month's picks, scored on a stored replay.

The pre-registration allows rules to be adjusted using 2019-2022 only, and the
holdout (2023 onwards) is run once with the final rules, so this refuses holdout
runs. Each variant is a different selection from the same stored monthly
assessments, so no new replay is needed: it reuses phase 2's pick returns (against
random picks from the same month's eligible companies) and its simulation of the
live portfolio rules (£200 a month, at most 10 holdings, costs).

Run on a survivors-only replay, results are flattered and only indicative; run them
again after phase 3 (docs/backtest-phase3.md) before changing any rule.

    python -m app.prototype.experiments --replay-db PATH [--run-id ID]
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from ..config import get_settings
from .measure import DRAWS, HORIZONS, load_market, load_months, performance, pick_returns, pick_summary, simulate
from .ranking import RULES
from .service import PrototypeError

FELL = 'The price fell'  # the ranking's sharp-fall risk text (a 126-session fall of 30% or more)


def _scored(month, statuses=('candidate',)):
    """Assessments with the given statuses, best score first (the live order)."""
    found = [a for a in month['assessments'] if a['status'] in statuses and a.get('score') is not None]
    return sorted(found, key=lambda a: (-a['score'], a['security_id']))


def _ids(assessments): return [a['security_id'] for a in assessments]


# Each variant maps a stored month to its picks. The first is the live rule.
VARIANTS = {
    'top3_live_rules': lambda m: m['picks'],
    'top10_by_score': lambda m: _ids(_scored(m)[:10]),
    'all_candidates': lambda m: _ids(_scored(m)),
    'top3_without_sharp_fallers': lambda m: _ids([a for a in _scored(m) if not any(r.startswith(FELL) for r in a.get('risks') or [])][:3]),
    'top3_strong_buy_only': lambda m: _ids([a for a in _scored(m) if a.get('recommendation') == 'strong_buy'][:3]),
    'top3_value_traps_allowed': lambda m: _ids([a for a in _scored(m, ('candidate', 'value_trap'))
                                                if (a.get('upside') or 0) >= RULES['minimum_upside']][:3]),
}


def run(replay_db, research_db, prototype_db=None, run_id=None, draws=DRAWS, variants=VARIANTS):
    run_id, holdout, months = load_months(replay_db, run_id)
    if holdout: raise PrototypeError('EXPERIMENTS_REFUSE_HOLDOUT')
    if not months: raise PrototypeError('MEASURE_NO_MONTHS')
    symbols = {e['qualified_symbol'] for m in months for e in m['eligible']}
    market = load_market(research_db, prototype_db, symbols, months[0]['cutoff'], date(months[-1]['cutoff'].year + 2, 1, 1))
    survivors_only = not any(str(e['security_id']).startswith('delisted:') for m in months for e in m['eligible'])
    results = {}
    for name, choose in variants.items():
        chosen = [m | {'picks': choose(m)} for m in months]
        rows = {h: pick_returns(chosen, market, h) for h in HORIZONS}
        summaries = {f'{h}m': pick_summary(rows[h], h, draws=draws) for h in HORIZONS}
        simulation = simulate(chosen, market)
        results[name] = {
            'average_picks_per_month': sum(len(m['picks']) for m in chosen) / len(chosen),
            'picks': {k: {f: v.get(f) for f in ('average_pick_return', 'average_eligible_return', 'skill_vs_eligible',
                                                 'random_pick_percentile', 'pick_hit_rate_vs_eligible')} for k, v in summaries.items()},
            'policy': performance(simulation['path'])}
    return {'run_id': run_id, 'months': len(months), 'first': months[0]['cutoff'].isoformat(), 'last': months[-1]['cutoff'].isoformat(),
            'survivors_only': survivors_only, 'variants': results,
            'note': ('Survivors only: flattered and indicative; rerun after phase 3 before changing a rule.' if survivors_only
                     else 'Includes delisted companies. Tuning window only; the holdout decides.')}


def table(result):
    """A plain-text comparison: skill against random picks and the simulated portfolio."""
    lines = [f"{result['months']} months {result['first']} to {result['last']} - {result['note']}",
             f"{'variant':30s} {'picks/mo':>8s} {'12m skill':>10s} {'interval':>17s} {'pctile':>6s} {'hit':>5s} {'MWR/yr':>7s} {'max DD':>7s} {'final GBP':>9s}"]
    for name, v in result['variants'].items():
        s = v['picks']['12m']; skill = s.get('skill_vs_eligible') or {}; p = v['policy'] or {}
        fmt = lambda x, spec='+.1%': '—' if x is None else format(x, spec)
        lines.append(f"{name:30s} {v['average_picks_per_month']:8.1f} {fmt(skill.get('mean')):>10s} "
                     f"{fmt(skill.get('low')) + '..' + fmt(skill.get('high')):>17s} {fmt(s.get('random_pick_percentile'), '.0%'):>6s} "
                     f"{fmt(s.get('pick_hit_rate_vs_eligible'), '.0%'):>5s} {fmt(p.get('money_weighted_annual')):>7s} "
                     f"{fmt(p.get('max_drawdown'), '.0%'):>7s} {fmt(p.get('final_value_gbp'), ',.0f'):>9s}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare pick-selection variants on a stored tuning-window replay')
    parser.add_argument('--replay-db', required=True, type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--json', action='store_true', help='print the full result as JSON instead of the table')
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        result = run(args.replay_db, settings.research_database_path, settings.prototype_database_path, args.run_id)
    except PrototypeError as exc:
        print(json.dumps({'status': 'failed', 'error': {'code': exc.code}}), file=sys.stderr); raise SystemExit(1) from None
    print(json.dumps(result, default=str, indent=1) if args.json else table(result))


if __name__ == '__main__':
    main()
