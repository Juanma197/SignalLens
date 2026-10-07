"""Milestone 42: bounded evidence assessment, never a panel or model executor."""
from collections import Counter
from datetime import timezone
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .active_catalogue import select_active_catalogue
from .financial_strength import (AUDIT_FIELDS, _report, _contract_results,
                                 _within_contract)
from .investment_research import (FAMILIES, FIELDS, CostAssumptions, TRACK_B_LABELS,
                                 coverage_audit, family_readiness, _immutable,
                                 InvestmentResearchError)

MAXIMUM_BYTES = 64 * 1024
SPEC_PATH = Path(__file__).with_name('track_b_panel_draft_v0.1.0.json')
PROHIBITED_ARRAYS = ('scores', 'rankings', 'candidates', 'recommendations',
                    'allocations', 'selections', 'paper_selections', 'vintages',
                    'prospective_vintages', 'validation_observations')


def reconcile_distribution(distribution, total):
    """Exact bins only: reject unknown keys, invalid counts and denominator gaps."""
    if set(distribution) - set(range(7)):
        raise InvestmentResearchError('invalid family bin')
    if type(total) is not int or total < 0 or any(
        type(v) is not int or v < 0 for v in distribution.values()
    ):
        raise InvestmentResearchError('invalid denominator count')
    bins = {str(k): distribution.get(k, 0) for k in range(7)}
    if sum(bins.values()) != total:
        raise InvestmentResearchError('family denominator does not reconcile')
    return {'semantics': 'exact_full_family_counts_not_cumulative',
            'exact_bins': bins, 'total': total,
            'fewer_than_three': sum(bins[str(k)] for k in range(3)),
            'at_least_four': sum(bins[str(k)] for k in range(4, 7)),
            'reconciled': True}


def assessment(*, research_db, production_db, decision_at):
    research_db, production_db = Path(research_db), Path(production_db)
    spec = json.loads(SPEC_PATH.read_text(encoding='utf-8'))
    spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    costs = CostAssumptions(**spec['costs']['draft_assumptions'])

    def build(_db):
        audit = coverage_audit(research_db=research_db, production_db=production_db,
                               decision_at=decision_at, max_samples=0)
        _, companies, _, _ = _report(research_db=research_db,
            production_db=production_db, decision_at=decision_at)
        evidence = audit['security_evidence']  # complete classified population
        total = audit['comparable_universe_count']
        distribution = Counter()
        patterns = Counter()
        families = {}
        for states in evidence.values():
            rows = {f: family_readiness(states, f) for f in FAMILIES}
            distribution[sum(r['full_family_ready'] for r in rows.values())] += 1
            patterns['|'.join(sorted(f for f, r in rows.items()
                                    if not r['full_family_ready'])) or 'none'] += 1
        for family in FAMILIES:
            rows = [family_readiness(s, family) for s in evidence.values()]
            families[family] = {
                key: sum(r[key] for r in rows) for key in
                ('any_input_available', 'minimum_calculable', 'full_family_ready')}
            families[family]['missing_required_inputs'] = dict(sorted(Counter(
                f for r in rows for f in r['missing_required_inputs']).items()))
            families[family]['denominator'] = total
        fields = {f: {'available': sum(s.get(f) == 'available' for s in evidence.values()),
                      'missing_or_withheld': sum(s.get(f) != 'available' for s in evidence.values()),
                      'reasons': dict(sorted(Counter(s.get(f, 'evidence_unavailable')
                          for s in evidence.values() if s.get(f) != 'available').items()))}
                  for f in FIELDS}
        active = select_active_catalogue(_db, as_of=decision_at.astimezone(
            timezone.utc).replace(tzinfo=None))
        identities = {str(r.qualified_symbol): str(r.security_id)
                      for r in active.listings.itertuples(index=False)}
        by_symbol = {}
        for c in companies:
            if (c['qualified_symbol'] in evidence and
                identities.get(c['qualified_symbol']) == c['security_id']):
                if c['qualified_symbol'] in by_symbol:
                    raise InvestmentResearchError('ambiguous component identity')
                by_symbol[c['qualified_symbol']] = c
        matched = list(by_symbol.values())
        uncovered = total - len(matched)
        components = {}
        for name in ('leverage', 'liquidity', 'coverage', 'cash_generation_debt_service'):
            counts = Counter(c['components'][name] for c in matched)
            counts['unavailable'] += uncovered
            components[name] = {s: counts[s] for s in ('ready', 'not_applicable', 'unavailable')}
        missing = {f: sum(f in c['missing_inputs'] for c in matched)
                   for f in AUDIT_FIELDS}
        blockers = [{'code': code, 'state': 'unresolved', 'affected_company_count': None,
                     'evidence': 'draft requirement; single-boundary input audit cannot certify it'}
                    for code in spec['preregistration_blockers']]
        return {
            'command': 'track-b-preregistration-assessment', 'decision_at': audit['decision_at'],
            'specification': {'version': spec['version'], 'sha256': spec_hash,
                              'status': spec['status']},
            'labels': TRACK_B_LABELS, 'read_only': True,
            'evidence_scope': 'stored_evidence_at_one_boundary_not_operator_findings',
            'family_distribution': reconcile_distribution(distribution, total),
            'families': families, 'fields': fields,
            'exact_company_blocker_counts': {
                'no_full_families': distribution.get(0, 0),
                'fewer_than_three_full_families': sum(distribution.get(k, 0) for k in range(3)),
                'incomplete_family': {f: total - r['full_family_ready'] for f, r in families.items()},
                'interpretation': 'Input-readiness deficiencies, not approved sample exclusions or history/outcome certification'},
            'missingness_patterns': [{'pattern': k, 'count': v} for k, v in sorted(patterns.items())],
            'classification_counts': audit['classification_counts'],
            'financial_strength': {'component_denominator': total,
                'component_evidence_unmatched': uncovered, 'components': components,
                'missing_inputs': missing, 'missing_input_denominator': len(matched),
                'missing_or_unassessed_inputs': {f: n + uncovered for f, n in missing.items()},
                'audit_readiness': {k: sum(c['readiness'][k] for c in matched) for k in
                    ('any_input_available', 'minimum_calculable', 'full_family_ready')},
                'withholding_observation_counts': dict(sorted(Counter(
                    o['withholding_reason'] for c in matched for o in c['observations']
                    if not o['usable']).items())),
                'candidate_contracts': {k: _contract_results(matched).get(k, 0) for k in
                    ('A_all_components_mandatory',
                     'B_leverage_liquidity_mandatory_coverage_optional',
                     'C_two_independent_components', 'D_debt_free_or_leveraged_branch')},
                'contract_evidence_denominator': len(matched), 'contract_selected': None,
                'accounting_review': spec['financial_strength']['review'],
                'contract_tradeoffs': spec['financial_strength']['contract_tradeoffs'],
                'interpretation': 'Component feasibility does not replace existing full-family semantics; liquidity does not supply debt, equity, gross interest or defensible EBIT. Contract counts are audit counterfactuals, not aligned-history factor readiness.'},
            'history_outcome_assessment': 'not_certified_no_returns_read',
            'draft_costs': asdict(costs), 'cost_configuration_hash': costs.configuration_hash,
            'blockers': blockers, 'unresolved_requirement_count': len(blockers),
            'preregistration_ready': False, 'feasibility_is_not_model_approval': True,
            'panel_persisted': False, 'model_executed': False,
            'bounds': {'maximum_compact_utf8_bytes': MAXIMUM_BYTES,
                       'maximum_missingness_patterns': 64, 'company_samples': 0},
            **{key: [] for key in PROHIBITED_ARRAYS}, 'validation_credit': 0}

    result = _immutable(research_db, production_db, build)
    return _within_contract(result, MAXIMUM_BYTES)
