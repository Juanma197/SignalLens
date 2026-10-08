"""Synthetic fixtures only; no operator databases or providers."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import subprocess
import sys
import duckdb
import pytest
from app import track_b_construction_feasibility as f

D=datetime(2026,10,5,0,30,tzinfo=timezone.utc)
OLD=datetime(2026,8,2,tzinfo=timezone.utc)
CAL=[date(2025,1,1),date(2025,3,31),date(2025,6,30),date(2025,9,30),date(2025,12,31)]
SOURCE={'state':'supported'}

def row(concept=f.OCF,q=1,**overrides):
    r=dict(security_id='s',concept=concept,taxonomy='us-gaap',fact_key=f'{concept}-{q}',cik='100',
        accession_number='filing',source_endpoint='companyfacts',unit='USD',currency=None,scale=None,
        period_start=CAL[0],period_end=CAL[q],public_at=OLD,retrieved_at=OLD,
        fiscal_year=2025,fiscal_period='FY',frame='CY2025',fiscal_year_start=CAL[0],fiscal_quarter=q,
        fiscal_calendar_source='calendar-note',duration_kind='ytd',context_scope='consolidated',
        context_dimensions='{}',accounting_basis='basis-v1',context_source='context-reference',
        revision_set_id='compatible-set',revision_status='current_compatible',revision_source='revision-reference',
        source_decimals='0',precision_source='XBRL-decimals',_value_signature=f'{concept}-{q}',_nonnegative=True,_amount_order_rank=q)
    for i in range(1,5):r[f'fiscal_quarter_end_{i}']=CAL[i]
    if concept not in (f.OCF,f.CAPEX):r['period_start']=None
    for kind in ('fiscal','context','revision','precision','borrowing'):r[kind+'_metadata_available_at']=OLD
    r.update(overrides);return r

def layer(rows):return f.assess_layer(rows,'raw_sec',{'s'},D,SOURCE)

def borrowing():
    return [row(c,component_members=json.dumps([m]),borrowing_universe_members='["st","current","noncurrent"]',
        borrowing_scope_source='debt-note') for c,m in zip(f.DEBT,['st','current','noncurrent'])]

def canonical(raw,materialized=None,controlled=False):
    r=deepcopy(raw);r.update(original_concept_or_field=r.pop('concept'),evidence_key='canonical',
        source_fact_key=r['fact_key'],canonical_field='unrestricted_cash',available_at=OLD,materialized_at=materialized,
        _provenance_valid=True,_operation_type='liquidity_canonical_materialization' if controlled else None,
        alias_contract_version='milestone-37-audited-alias-contracts-1',accession_or_source_identifier='filing',reliability_state='usable')
    if controlled and materialized:r['available_at']=max(OLD,materialized)
    return r

def test_complete_chains_counted_without_amounts():
    r=layer([row(c,q) for c in (f.OCF,f.CAPEX) for q in range(1,5)])
    assert r['flows']==dict(compatible_direct_quarter_period_candidates=0,compatible_first_quarter_ytd_candidates=2,
        compatible_cumulative_to_quarter_candidates=6,missing_or_incompatible_cumulative_predecessor_candidates=0,
        compatible_ocf_ttm_candidates=1,compatible_capex_ttm_candidates=1,compatible_paired_ttm_candidates=1,
        capex_pair_order_unproven_candidates=0,capex_decreasing_cumulative_pair_count=0)
    assert r['certified_formula_count']==0

def test_fy_fp_frame_shapes_are_not_proofs():
    rows=[row(q=q) for q in range(1,5)]
    for r in rows:
        for k in f.OPTIONAL:
            if k not in ('fiscal_year','fiscal_period','frame','is_amendment','is_revision'):r.pop(k,None)
    r=layer(rows);m=r['missing_or_unproven_metadata']
    assert all(m[k]==4 for k in ('missing_or_invalid_fiscal_metadata_rows','missing_or_invalid_context_metadata_rows',
        'missing_revision_metadata_rows','unknown_precision_rows'))
    assert r['flows']['compatible_ocf_ttm_candidates']==r['flows']['compatible_cumulative_to_quarter_candidates']==0

def test_gaps_overlaps_disagreements_and_declared_conflicts_distinct():
    r=layer([row(),row(q=2,period_start=date(2025,4,2)),row(q=3,period_start=date(2025,6,20)),
        row(revision_status='conflicting',_value_signature='different')])
    d=r['revision_and_shape_diagnostics']
    assert d['shape_gap_pairs']==d['shape_overlap_pairs']==1
    assert d['stored_value_disagreement_groups']==d['conflicting_revision_groups']==d['unresolved_multiple_revision_groups']==1
    assert r['flows']['compatible_cumulative_to_quarter_candidates']==0

def test_no_latest_filing_wins_and_explicit_supersession():
    r=layer([row(),row(_value_signature='changed',revision_source=None)])
    assert r['revision_and_shape_diagnostics']['stored_value_disagreement_groups']==1
    assert r['revision_and_shape_diagnostics']['conflicting_revision_groups']==0
    assert r['flows']['compatible_first_quarter_ytd_candidates']==0
    r=layer([row(revision_status='superseded'),row(_value_signature='changed')])
    assert r['flows']['compatible_first_quarter_ytd_candidates']==r['row_counts']['visible_superseded_rows']==1

def test_no_cross_context_or_revision_basis_pairing():
    rows=[row(c,q) for c in (f.OCF,f.CAPEX) for q in range(1,5)]
    for r in rows:
        if r['concept']==f.CAPEX:r['accounting_basis']='different'
    r=layer(rows)
    assert r['flows']['compatible_ocf_ttm_candidates']==r['flows']['compatible_capex_ttm_candidates']==1
    assert r['flows']['compatible_paired_ttm_candidates']==0
    rows[1]['revision_set_id']='unrelated'
    assert layer(rows)['flows']['compatible_ocf_ttm_candidates']==0

def test_exact_cash_meaning_and_unknown_precision():
    cash=row(f.CASH)
    r=layer([cash,row('CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents')])
    assert r['cash']['visible_exact_source_cash_rows']==r['cash']['compatible_direct_cash_candidate_rows']==1
    cash['source_decimals']=None;r=layer([cash])
    assert r['cash']['unproven_or_incompatible_direct_cash_rows']==r['missing_or_unproven_metadata']['unknown_precision_rows']==1

def test_debt_disjoint_complete_repeated_overlap_and_incomplete():
    rows=borrowing();r=layer(rows)['borrowing']
    assert r['proven_disjoint_groups']==r['proven_complete_groups']==r['compatible_borrowing_total_candidate_groups']==1
    assert layer(rows+[deepcopy(rows[0])])['borrowing']['compatible_borrowing_total_candidate_groups']==1
    rows[1]['component_members']='["st","current"]';r=layer(rows)['borrowing']
    assert r['proven_overlapping_groups']==1 and r['compatible_borrowing_total_candidate_groups']==0
    r=layer(borrowing()[:2])['borrowing']
    assert r['proven_incomplete_groups']==r['missing_required_component_groups']==1

def test_debt_tags_and_different_contexts_cannot_prove_coverage():
    r=layer([row(c) for c in f.DEBT])['borrowing']
    assert r['unknown_overlap_groups']==r['unknown_completeness_groups']==1 and r['compatible_borrowing_total_candidate_groups']==0
    rows=borrowing();rows[1]['context_dimensions']='{"segment":"x"}'
    assert layer(rows)['borrowing']['unknown_overlap_groups']==1

def test_controlled_canonical_historically_invisible_and_backdating_rejected():
    later=D+timedelta(days=1);r=canonical(row(f.CASH),later,True)
    result=f.assess_layer([r],'canonical',{'s'},D,SOURCE)
    assert result['row_counts']['post_decision_rows']==1 and result['cash']['compatible_direct_cash_candidate_rows']==0
    r['available_at']=OLD;result=f.assess_layer([r],'canonical',{'s'},D,SOURCE)
    assert result['row_counts']['visibility_unproven_rows']==result['missing_or_unproven_metadata']['availability_rule_mismatch']==1
    r['available_at']=later;r['materialized_at']=None
    assert f.input_availability(r,'canonical')[1]=='missing_aware_materialized_at'

def test_raw_legacy_preserved_and_unknown_control_never_downgraded():
    raw=row(f.CASH,available_at=D+timedelta(days=3))
    assert f.input_availability(raw,'raw_sec')==(OLD,None)
    legacy=canonical(raw,D+timedelta(days=1))
    assert f.input_availability(legacy,'canonical')==(OLD,None)
    assert f.assess_layer([legacy],'canonical',{'s'},D,SOURCE)['cash']['compatible_direct_cash_candidate_rows']==1
    legacy['_provenance_valid']=False
    assert f.input_availability(legacy,'canonical')[1]=='control_provenance_unproven'

def test_execution_and_future_persistence_never_backdated():
    execution=D+timedelta(days=3);creation=execution+timedelta(seconds=1)
    timing=f.assessment_timing([OLD],execution,creation)
    assert timing==dict(input_available_at=OLD.isoformat(),executed_at=execution.isoformat(),future_persisted_revision_available_at=creation.isoformat())
    with pytest.raises(f.FeasibilityError):f.assessment_timing([OLD],execution,OLD)
    with pytest.raises(f.FeasibilityError):f.assessment_timing([creation],execution)

def create(db,table,columns=None):
    columns=columns or f.SOURCES[table]
    def kind(c):
        if c in ('is_current','eligible','active','is_amendment','is_revision'):return 'BOOLEAN'
        if c.endswith('_at') or c in ('effective_from','effective_to','first_seen_at','last_seen_at'):return 'TIMESTAMPTZ'
        if c in ('period_start','period_end','instant_date','fiscal_year_start') or c.startswith('fiscal_quarter_end_'):return 'DATE'
        if c in ('fiscal_quarter','fiscal_year','scale'):return 'INTEGER'
        return 'VARCHAR'
    defs=[c+' '+kind(c) for c in columns]
    if table not in f.IDENTITY_TABLES:defs+=['value DOUBLE','payload_json VARCHAR','provenance JSON']
    db.execute('CREATE TABLE '+table+'('+','.join(defs)+')')

def insert(db,table,r):
    actual={x[0] for x in db.execute('DESCRIBE '+table).fetchall()};r={k:v for k,v in r.items() if k in actual}
    db.execute('INSERT INTO '+table+'('+','.join(r)+') VALUES ('+','.join('?' for _ in r)+')',list(r.values()))

def fixture(tmp_path,full=True):
    paths=(tmp_path/'research.duckdb',tmp_path/'production.duckdb')
    for path in paths:
        with duckdb.connect(str(path)) as db:
            for table in f.SOURCES:
                cols=None if full or table in f.IDENTITY_TABLES else tuple(c for c in f.SOURCES[table] if c not in f.OPTIONAL)
                create(db,table,cols)
            db.execute('CREATE TABLE model_outputs(secret VARCHAR)');db.execute("INSERT INTO model_outputs VALUES ('DO_NOT_READ')")
    with duckdb.connect(str(paths[0])) as db:
        insert(db,'security_listings',dict(security_id='s',cik='100'))
        insert(db,'security_classification_evidence',dict(security_id='s',cik='100',security_type='us_operating_company',public_at=OLD,retrieved_at=OLD,available_at=OLD,is_current=True))
        for c in (f.OCF,f.CAPEX):
            for q in range(1,5):insert(db,'sec_facts',row(c,q,value=10*q,payload_json='POISON_PAYLOAD'))
        insert(db,'sec_facts',row(f.CASH,value=50))
        for r in borrowing():insert(db,'sec_facts',dict(r,value=12))
    return paths

def test_readonly_file_counts_controls_and_redaction(tmp_path):
    paths=fixture(tmp_path);before=[f.history.fingerprint(p) for p in paths]
    r=f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert [f.history.fingerprint(p) for p in paths]==before
    raw=r['databases']['research']['raw_sec']
    assert raw['flows']['compatible_paired_ttm_candidates']==raw['borrowing']['compatible_borrowing_total_candidate_groups']==1
    assert r['databases']['production']['raw_sec']['row_counts']['stored_relevant_rows']==0
    assert r['status']=='proposed_not_authorized' and r['unresolved_requirement_count']==8
    assert all(b['state']=='unresolved' for b in r['blockers']) and all(r[k]==[] for k in f.PROHIBITED_ARRAYS)
    text=json.dumps(r,sort_keys=True,separators=(',',':'))
    assert len(text.encode())==r['compact_utf8_bytes']<=f.MAX_BYTES
    assert 'POISON_PAYLOAD' not in text and 'DO_NOT_READ' not in text
    assert not r['derived_amounts_computed'] and not r['derived_evidence_persisted'] and r['future_persisted_revision_available_at'] is None

def test_real_schema_omissions_are_exact_unknown_counts(tmp_path):
    paths=fixture(tmp_path,False);r=f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)['databases']['research']['raw_sec']
    assert r['missing_or_unproven_metadata']['missing_or_invalid_fiscal_metadata_rows']==8
    assert r['missing_or_unproven_metadata']['missing_revision_metadata_rows']==12
    assert r['flows']['compatible_paired_ttm_candidates']==r['cash']['compatible_direct_cash_candidate_rows']==0
    assert r['borrowing']['unknown_completeness_groups']==1

def test_views_and_missing_keys_are_unknown_not_zero(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:db.execute('DROP TABLE sec_facts');db.execute("CREATE VIEW sec_facts AS SELECT 'POISON' AS security_id")
    r=f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert r['databases']['production']['raw_sec']['counts'] is None
    with duckdb.connect(str(paths[1])) as db:db.execute('DROP VIEW sec_facts');db.execute('CREATE TABLE sec_facts(security_id VARCHAR)')
    r=f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert r['databases']['production']['raw_sec']['counts'] is None

@pytest.mark.parametrize('bound,value,reason',[('MAX_ROWS',1,'ROW_LIMIT'),('MAX_TOTAL_ROWS',1,'TOTAL_ROW_LIMIT'),('MAX_WORK',1,'WORK_LIMIT'),('MAX_BYTES',1,'REPORT_LIMIT')])
def test_bounds_fail_closed_unchanged_files(tmp_path,monkeypatch,bound,value,reason):
    paths=fixture(tmp_path);before=[f.history.fingerprint(p) for p in paths];monkeypatch.setattr(f,bound,value)
    with pytest.raises(f.FeasibilityError,match=reason):f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert [f.history.fingerprint(p) for p in paths]==before

def test_cell_and_group_bounds(tmp_path,monkeypatch):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute('UPDATE sec_facts SET context_source=?',['x'*1025])
    with pytest.raises(f.FeasibilityError,match='CELL_LIMIT'):f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    monkeypatch.setattr(f,'MAX_GROUP_ROWS',1)
    with pytest.raises(f.FeasibilityError,match='GROUP_LIMIT'):layer([row(),row()])

def test_after_hashes_independent_after_read_and_hash_failures(tmp_path,monkeypatch):
    paths=fixture(tmp_path);calls=[];original=f.history.fingerprint
    def fingerprint(path):
        calls.append(path)
        if len(calls)==3:raise OSError('secret path')
        return original(path)
    monkeypatch.setattr(f.history,'fingerprint',fingerprint)
    monkeypatch.setattr(f,'read_table',lambda *a,**kw: (_ for _ in ()).throw(f.FeasibilityError('injected')))
    with pytest.raises(f.FeasibilityError,match='HASH_VERIFICATION_FAILED'):f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]

def test_cli_errors_redacted(tmp_path):
    result=subprocess.run([sys.executable,'-m','app.track_b_construction_feasibility','--research-db',str(tmp_path/'SECRET'),
        '--production-db',str(tmp_path/'OTHER_SECRET'),'--decision-at',D.isoformat()],capture_output=True)
    assert result.returncode==1 and result.stdout==b''
    assert json.loads(result.stderr)=={'error':{'code':'TRACK_B_CONSTRUCTION_FEASIBILITY_FAILED'}}


def test_capex_decrease_and_order_unknown_withheld():
    r=layer([row(f.CAPEX,1,_amount_order_rank=2),row(f.CAPEX,2,_amount_order_rank=1)])
    assert r['flows']['capex_decreasing_cumulative_pair_count']==1
    assert r['flows']['compatible_cumulative_to_quarter_candidates']==0
    r=layer([row(f.CAPEX,1),row(f.CAPEX,2,_amount_order_rank=None)])
    assert r['flows']['capex_pair_order_unproven_candidates']==1

def test_later_proof_metadata_does_not_certify_historical_context():
    r=layer([row(context_metadata_available_at=D+timedelta(days=1))])
    assert r['missing_or_unproven_metadata']['missing_or_invalid_context_metadata_rows']==1
    assert r['flows']['compatible_first_quarter_ytd_candidates']==0

def test_controlled_provenance_extracted_in_sql(tmp_path):
    paths=fixture(tmp_path)
    late=D+timedelta(days=1)
    r=canonical(row(f.CASH),late,True)
    r.update(provenance=json.dumps({'operation_type':'liquidity_canonical_materialization'}),value=50)
    with duckdb.connect(str(paths[0])) as db:insert(db,'canonical_factor_evidence',r)
    report=f.assess(research_db=paths[0],production_db=paths[1],decision_at=D)
    assert report['databases']['research']['canonical']['row_counts']['post_decision_rows']==1
    assert report['databases']['research']['canonical']['cash']['compatible_direct_cash_candidate_rows']==0

def test_document_no_duplicate_sections_or_broken_table_rows():
    from pathlib import Path
    text=(Path(__file__).parents[2]/'docs/track-b-accounting-construction-proposal.md').read_text()
    headings=[line for line in text.splitlines() if line.startswith('#')]
    assert len(headings)==len(set(headings))
    expected=None
    for line in text.splitlines():
        if line.startswith('|'):
            if expected is None:expected=line.count('|')
            assert line.count('|')==expected and line.endswith('|')
        else:expected=None


def test_borrowing_overlap_proof_independent_of_completeness():
    rows=borrowing()
    for r in rows:r['borrowing_universe_members']=None
    result=layer(rows)['borrowing']
    assert result['proven_disjoint_groups']==1
    assert result['unknown_completeness_groups']==1
    assert result['unknown_overlap_groups']==0
    assert result['compatible_borrowing_total_candidate_groups']==0


def test_duplicate_component_proof_order_does_not_change_unknown_result():
    rows=borrowing();bad=dict(rows[0],borrowing_scope_source=None)
    assert layer(rows+[bad])['borrowing']['unknown_overlap_groups']==1
    assert layer([bad]+rows)['borrowing']['unknown_overlap_groups']==1
