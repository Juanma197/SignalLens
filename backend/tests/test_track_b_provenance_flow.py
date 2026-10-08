"""Synthetic offline flow witnesses; never operator evidence."""
import json
from app import track_b_provenance_replay as r
from test_track_b_provenance_replay import fixture, parsed, databases, D


def test_cash_cannot_consume_flow_budget_and_values_do_not_select():
    pair,_,rows=fixture()
    cash=[dict(rows[0],concept='CashAndCashEquivalentsAtCarryingValue',fact_key='cash'+str(i)) for i in range(24)]
    selected,info=r.select_pilot(pair,cash+rows,D,'flow-focused')
    other,alternate=r.select_pilot(pair[::-1],[dict(x,value='unused') for x in (cash+rows)[::-1]],D,'flow-focused')
    assert info==alternate and [x['fact_key'] for x in selected[1]]==[x['fact_key'] for x in other[1]]
    assert len(selected[1])==2 and info['flow_witness_concepts']==[r.feasibility.OCF]
    assert not info['fiscal_start_certified'] and not info['adjacent_fiscal_quarters_certified']


def test_no_cash_fallback_or_cross_concept_start_unit_or_same_end():
    pair,_,rows=fixture()
    for changed in ([dict(x,concept='CashAndCashEquivalentsAtCarryingValue') for x in rows],
        [rows[0],dict(rows[1],concept=r.feasibility.CAPEX)],
        [rows[0],dict(rows[1],period_start='2025-04-01')],
        [rows[0],dict(rows[1],unit='EUR')], [rows[0],dict(rows[1],period_end=rows[0]['period_end'])]):
        selected,info=r.select_pilot(pair,changed,D,'flow-focused')
        assert selected is None and info['state']=='no_qualifying_pair'


def test_duplicate_groups_preserved_and_over_bound_not_truncated():
    pair,_,rows=fixture()
    duplicates=[dict(rows[0],fact_key='duplicate'+str(i)) for i in range(22)]
    assert len(r.select_pilot(pair,rows+duplicates,D,'flow-focused')[0][1])==24
    assert r.select_pilot(pair,rows+duplicates+[dict(rows[0],fact_key='overflow')],D,'flow-focused')[0] is None


def test_manifest_duplicates_unsafe_and_missing_names():
    payloads,rows=parsed();pair,_,_=fixture();recent=payloads[1]['filings']['recent']
    for key in recent:recent[key].append(recent[key][0])
    recent['primaryDocument'][1]='../rejected.htm'
    manifest=r.filing_pilot_manifest(payloads[1],rows,pair)
    refs=manifest['entries'][0]['retained_document_locators']
    assert len(refs)==2 and refs[0]['primary_document']=='never-follow.htm' and refs[1]['primary_document'] is None
    assert '../rejected' not in json.dumps(manifest) and not manifest['retrieval_authorized']
    del recent['primaryDocument']
    assert r.filing_pilot_manifest(payloads[1],rows,pair)['entries'][0]['retained_document_locators'][0]['primary_document'] is None


def test_readonly_flow_end_to_end(tmp_path):
    paths=databases(tmp_path)
    report=r.run(research_db=paths[0],production_db=paths[1],decision_at=D.isoformat(),selection_mode='flow-focused')
    assert report['execution_state']=='completed', report['errors']
    assert report['pilot']['selected_stored_rows']==2
    assert len(report['pilot']['proposed_filing_manifest']['entries'])==1
    assert all(x['unchanged'] for x in report['database_hashes'].values())


def test_selection_work_limit_is_refusal_not_false_absence(monkeypatch):
    import pytest
    pair,_,rows=fixture()
    monkeypatch.setattr(r, 'MAX_MANIFEST', 1)
    candidates=[dict(rows[0],period_start='2024-'+str(i),fact_key=str(i)) for i in range(3)]
    with pytest.raises(r.ReplayError,match='FLOW_SELECTION_WORK_LIMIT'):
        r.select_pilot(pair,candidates,D,'flow-focused')


def test_checked_in_verifier_flow_mode(tmp_path):
    from test_track_b_provenance_verification import runner
    paths=databases(tmp_path); ns,_=runner()
    root=__import__('pathlib').Path(__file__).resolve().parents[2]
    summary=ns['verify'](root,dict(research=paths[0],production=paths[1]),tmp_path/'reports',D.isoformat(),True,'flow-focused')
    assert summary['execution_state']=='completed', summary['errors']
    result=json.loads((tmp_path/'reports'/'track-b-provenance-replay.json').read_text())
    assert result['pilot']['selection_mode']=='flow-focused'
    assert result['pilot']['proposed_filing_manifest']['entries']
