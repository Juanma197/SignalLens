"""Tuning experiments choose other picks from the same stored months, and never
touch the holdout."""
import pytest

from app.prototype import experiments
from app.prototype.service import PrototypeError


def assessment(sid, status, score, upside=0.5, recommendation=None, risks=()):
    return {'security_id': sid, 'status': status, 'score': score, 'upside': upside, 'recommendation': recommendation, 'risks': list(risks)}


MONTH = {'picks': ['a', 'b', 'c'], 'assessments': [
    assessment('a', 'candidate', 0.9, recommendation='strong_buy', risks=['The price fell 45% over 126 sessions; the market may know something.']),
    assessment('b', 'candidate', 0.8, recommendation='buy'),
    assessment('c', 'candidate', 0.7, recommendation='strong_buy'),
    assessment('d', 'candidate', 0.6, recommendation='strong_buy'),
    assessment('t', 'value_trap', 0.95), assessment('u', 'value_trap', 0.99, upside=0.05),
    assessment('n', 'not_undervalued', 0.1)]}


def test_variants_select_from_the_stored_assessments():
    pick = {name: rule(MONTH) for name, rule in experiments.VARIANTS.items()}
    assert pick['top3_live_rules'] == ['a', 'b', 'c']
    assert pick['all_candidates'] == pick['top10_by_score'] == ['a', 'b', 'c', 'd']
    assert pick['top3_without_sharp_fallers'] == ['b', 'c', 'd']
    assert pick['top3_strong_buy_only'] == ['a', 'c', 'd']
    assert pick['top3_value_traps_allowed'] == ['t', 'a', 'b']   # the trap below the minimum upside stays out


def test_experiments_refuse_the_holdout(monkeypatch):
    monkeypatch.setattr(experiments, 'load_months', lambda *a: ('run', True, [{'cutoff': None}]))
    with pytest.raises(PrototypeError) as error: experiments.run('replay', 'research')
    assert error.value.code == 'EXPERIMENTS_REFUSE_HOLDOUT'
