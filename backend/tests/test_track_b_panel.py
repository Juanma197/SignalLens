from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import duckdb
import pytest

from app.investment_evidence import initialize_schema
from app.financial_strength import _visible, _within_contract
from app.investment_research import FAMILIES, family_readiness, InvestmentResearchError
from app.track_b_panel import (assessment, reconcile_distribution, MAXIMUM_BYTES,
                              PROHIBITED_ARRAYS, SPEC_PATH)
from model_readiness_fixture import create_research_fixture

DECISION = datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)


def fixture(tmp_path):
    research = tmp_path / 'research.duckdb'
    production = tmp_path / 'production.duckdb'
    create_research_fixture(research, per_region=5)
    with duckdb.connect(str(production)) as db:
        db.execute('CREATE TABLE marker(value INTEGER)')
    with duckdb.connect(str(research)) as db:
        initialize_schema(db)
        for i in range(5):
            stamp = DECISION if i < 4 else DECISION + timedelta(microseconds=1)
            db.execute('''INSERT INTO security_classification_evidence(
                evidence_key,security_id,qualified_symbol,security_type,
                classification_reason,evidence_source_family,source_record_identifier,
                durable_identifier,public_at,retrieved_at,available_at,materialized_at,
                confidence_category,review_required,provenance,is_current)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,true)''',
                [f'c{i}', f'security-us-{i}', f'ALPHA{i}.US', 'us_operating_company',
                 'concordant_authoritative_evidence', 'stored_evidence_hierarchy',
                 f'source-{i}', f'security-us-{i}', stamp, stamp, stamp, stamp,
                 'high', False, json.dumps({'fixture': True})])
    return research, production


def test_supplied_denominator_residual_and_exact_bins():
    # Operator aggregates cannot reveal individual 0/1/2 counts.
    assert 71 - sum((18, 30, 14, 0)) == 9
    with pytest.raises(InvestmentResearchError):
        reconcile_distribution({3: 18, 4: 30, 5: 14, 6: 0}, 71)
    result = reconcile_distribution({0: 2, 1: 3, 2: 4, 3: 18, 4: 30, 5: 14}, 71)
    assert result['fewer_than_three'] == 9 and result['at_least_four'] == 44
    assert result['exact_bins']['3'] == 18
    for counts, total in [({7: 1}, 1), ({0: -1}, -1), ({0: True}, 1)]:
        with pytest.raises(InvestmentResearchError): reconcile_distribution(counts, total)
    assert reconcile_distribution({}, 0)['reconciled']


def test_missingness_and_liquidity_do_not_make_financial_family_ready():
    states = {x: 'available' for x in FAMILIES['financial_strength'] if x != 'interest_expense'}
    states.update(current_assets='available', current_liabilities='available', cash='available')
    result = family_readiness(states, 'financial_strength')
    assert result['any_input_available'] and result['minimum_calculable']
    assert not result['full_family_ready']
    assert result['missing_required_inputs'] == ['interest_expense']
    assert not family_readiness({}, 'financial_strength')['minimum_calculable']


def test_point_in_time_equality_and_missing_timestamps():
    row = dict.fromkeys(('public_at', 'retrieved_at', 'available_at'), DECISION)
    assert _visible(row, DECISION) == (True, None)
    for field in row:
        assert not _visible(row | {field: None}, DECISION)[0]
    later = DECISION + timedelta(microseconds=1)
    assert not _visible(row | {'retrieved_at': later, 'available_at': later}, DECISION)[0]
    assert not _visible(dict.fromkeys(row, DECISION.replace(tzinfo=None)), DECISION)[0]


def test_deterministic_complete_immutable_bounded_assessment(tmp_path):
    research, production = fixture(tmp_path)
    before = (research.read_bytes(), production.read_bytes())
    kwargs = dict(research_db=research, production_db=production, decision_at=DECISION)
    first = assessment(**kwargs)
    assert assessment(**kwargs) == first
    assert (research.read_bytes(), production.read_bytes()) == before
    total = first['family_distribution']['total']
    assert total == 4  # fifth classification is one microsecond too late
    assert sum(first['family_distribution']['exact_bins'].values()) == total
    for data in first['fields'].values():
        assert data['available'] + data['missing_or_withheld'] == total
        assert sum(data['reasons'].values()) == data['missing_or_withheld']
    for data in first['financial_strength']['components'].values():
        assert sum(data.values()) == total
    for key in PROHIBITED_ARRAYS: assert first[key] == []
    assert first['validation_credit'] == 0
    assert not first['preregistration_ready'] and not first['model_executed']
    assert first['unresolved_requirement_count'] == len(first['blockers']) == 8
    assert all(b['affected_company_count'] is None for b in first['blockers'])
    assert first['compact_utf8_bytes'] <= MAXIMUM_BYTES
    assert first['database_fingerprints']['production']['unchanged']
    result = subprocess.run([sys.executable, '-m', 'app.investment_research_cli',
        'track-b-preregistration-assessment', '--research-db', str(research),
        '--production-db', str(production), '--decision-at', DECISION.isoformat()],
        capture_output=True, text=True)
    assert result.returncode == 0 and result.stderr == ''
    assert json.loads(result.stdout) == first
    assert (research.read_bytes(), production.read_bytes()) == before
    with pytest.raises(InvestmentResearchError):
        assessment(**(kwargs | {'decision_at': DECISION.replace(tzinfo=None)}))
    with pytest.raises(ValueError):
        assessment(**(kwargs | {'production_db': research}))


def test_size_contract_fails_closed_and_draft_has_no_approved_contract():
    with pytest.raises(InvestmentResearchError):
        _within_contract({'text': 'é' * MAXIMUM_BYTES}, MAXIMUM_BYTES)
    spec = json.loads(SPEC_PATH.read_text())
    assert spec['weights'] is None and spec['financial_strength']['selected_contract'] is None
    assert spec['outcomes']['draft_horizons_completed_sessions'] == [126, 252]
    assert {f['family'] for f in spec['factors']} == set(FAMILIES)


def test_cli_explicit_paths_and_redacted_failure(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'app.investment_research_cli',
        'track-b-preregistration-assessment', '--research-db', str(tmp_path/'missing'),
        '--production-db', str(tmp_path/'other'), '--decision-at', DECISION.isoformat()],
        capture_output=True, text=True)
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr)['status'] == 'failed'
    assert str(tmp_path) not in result.stderr


def test_empty_population_and_identity_mismatch_fail_closed(tmp_path):
    research, production = fixture(tmp_path)
    with duckdb.connect(str(research)) as db:
        db.execute("UPDATE security_classification_evidence SET security_id='other' WHERE qualified_symbol='ALPHA0.US'")
    report = assessment(research_db=research, production_db=production, decision_at=DECISION)
    assert report['family_distribution']['total'] == 3  # mismatched identity is excluded
    assert report['financial_strength']['contract_selected'] is None
    with duckdb.connect(str(research)) as db:
        db.execute("UPDATE security_classification_evidence SET security_type='classification_unavailable'")
    report = assessment(research_db=research, production_db=production, decision_at=DECISION)
    assert report['family_distribution']['total'] == 0
    assert report['family_distribution']['reconciled']
    assert all(v == 0 for v in report['financial_strength']['candidate_contracts'].values())
    assert not report['preregistration_ready']


def test_complete_denominator_precedes_output_bounds(tmp_path, monkeypatch):
    import app.track_b_panel as panel
    from app.investment_research import FIELDS
    research, production = fixture(tmp_path)
    evidence = {}
    expected = {k: 0 for k in range(7)}
    for i in range(71):
        # Deterministic, heterogeneous stored-field proxies; no operator data.
        available = {f for j, f in enumerate(FIELDS) if (i + j) % 7 != 0}
        evidence[f'FIXTURE{i}.US'] = {f: 'available' if f in available else
            'evidence_unavailable' for f in FIELDS}
        expected[sum(set(required) <= available for required in FAMILIES.values())] += 1
    monkeypatch.setattr(panel, 'coverage_audit', lambda **kw: {
        'security_evidence': evidence, 'comparable_universe_count': 71,
        'decision_at': DECISION.isoformat(), 'classification_counts': {'us_operating_company': 71}})
    report = assessment(research_db=research, production_db=production, decision_at=DECISION)
    assert report['family_distribution']['exact_bins'] == {str(k): v for k, v in expected.items()}
    assert report['financial_strength']['component_evidence_unmatched'] == 71
    assert report['financial_strength']['missing_input_denominator'] == 0
    assert all(n == 0 for n in report['financial_strength']['missing_inputs'].values())
    assert all(n == 71 for n in report['financial_strength']['missing_or_unassessed_inputs'].values())
    assert sum(p['count'] for p in report['missingness_patterns']) == 71
    assert report['compact_utf8_bytes'] < MAXIMUM_BYTES
    assert 'security_evidence' not in report and 'companies' not in report
