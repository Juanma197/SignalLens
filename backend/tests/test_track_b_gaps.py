"""Offline synthetic files only. Never opens operator data or providers."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from app import track_b_gaps as g
from app import track_b_history as h
from app.track_b_panel import PROHIBITED_ARRAYS

D = datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)
QUARTERS = [(date(2025,1,1),date(2025,3,31)), (date(2025,4,1),date(2025,6,30)),
            (date(2025,7,1),date(2025,9,30)), (date(2025,10,1),date(2025,12,31))]


def create(db, table):
    def kind(column):
        if column in ('period_start', 'period_end', 'instant_date'):
            return 'DATE'
        if column in h.DATE_COLUMNS or column == 'updated_at':
            return 'TIMESTAMPTZ'
        if column in ('is_current', 'transaction_succeeded', 'active', 'eligible'):
            return 'BOOLEAN'
        if column == 'byte_count':
            return 'BIGINT'
        return 'VARCHAR'
    db.execute('CREATE TABLE '+table+'('+','.join(c+' '+kind(c) for c in g.SOURCES[table])+
               ',value VARCHAR,payload_json VARCHAR)')


def add(db, table, **row):
    row.update(value='POISON_AMOUNT', payload_json='POISON_PAYLOAD')
    db.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', list(row.values()))


def raw(db, field, sid='s0', start=None, end=date(2025,12,31), **overrides):
    row = dict(security_id=sid, cik='100', taxonomy='us-gaap', concept=h.FIELDS[field][2][0],
        unit='USD', currency='USD', period_start=start, period_end=end,
        public_at=D, retrieved_at=D, accession_number='accession', source_endpoint='companyfacts')
    row.update(overrides)
    add(db, 'sec_facts', **row)


def canonical(db, field, sid='s0', start=None, end=date(2025,12,31), **overrides):
    row = dict(security_id=sid, canonical_field=field, original_concept_or_field=h.FIELDS[field][2][0],
        unit='USD', currency='USD', period_start=start, period_end=end,
        public_at=D, retrieved_at=D, available_at=D, materialized_at=D,
        reliability_state='usable', alias_contract_version='fixture',
        accession_or_source_identifier='accession', source_fact_key='fact')
    row.update(overrides)
    add(db, 'canonical_factor_evidence', **row)


def checkpoint(db, sid, endpoints=('submissions','companyfacts'), stamp=D):
    identity = dict(run_id='run-'+sid, lineage_id='lineage', plan_id='plan',
        operation_type='sec_liquidity_evidence_ingestion', operation_contract_version='1.0.0',
        concept_contract_hash='hash')
    add(db, 'sec_liquidity_runs', **identity)
    add(db, 'sec_liquidity_checkpoints', **identity, security_id=sid, cik='100',
        status='completed', transaction_succeeded=True, updated_at=stamp)
    for endpoint in endpoints:
        add(db, 'sec_liquidity_raw_provenance', **identity, security_id=sid, cik='100',
            endpoint_class=endpoint, retrieved_at=stamp, response_sha256='a'*64,
            byte_count=100, parser_version='fixture')


def fixture(tmp_path):
    paths = (tmp_path/'research.duckdb', tmp_path/'production.duckdb')
    for path in paths:
        with duckdb.connect(str(path)) as db:
            for table in g.SOURCES:
                create(db, table)
            db.execute("CREATE TABLE model_outputs(secret VARCHAR)")
            db.execute("INSERT INTO model_outputs VALUES ('DO_NOT_READ')")
    with duckdb.connect(str(paths[0])) as db:
        for i in range(500):
            add(db, 'security_listings', security_id='s'+str(i), active=True,
                cik='100' if i==0 else '200' if i==1 else '300' if i in (2,72) else None)
        add(db, 'sec_issuers', security_id='s1', cik='201')
        for sid in ['s'+str(i) for i in range(100)] + ['ghost']:
            ordinary = sid=='ghost' or int(sid[1:])<70
            add(db, 'security_classification_evidence', security_id=sid,
                security_type='us_operating_company' if ordinary else 'bank',
                public_at=D, retrieved_at=D, available_at=D, is_current=True,
                durable_identifier=sid)
        for s,e in QUARTERS:
            raw(db, 'operating_cash_flow', start=s, end=e)
            canonical(db, 'operating_cash_flow', start=s, end=e)
        # No aligned standalone capex periods; annual/YTD are not transformed.
        raw(db, 'capital_expenditure', start=date(2025,1,1), end=date(2025,6,30))
        raw(db, 'capital_expenditure', start=date(2025,1,1), end=date(2025,9,30))
        raw(db, 'capital_expenditure', start=date(2025,1,1))
        canonical(db, 'capital_expenditure', start=date(2025,1,1))
        raw(db, 'cash_and_cash_equivalents')
        raw(db, 'current_debt', concept='LongTermDebtCurrent')
        canonical(db, 'cash_and_cash_equivalents', canonical_field='unrestricted_cash')
        for _ in range(1322):
            canonical(db, 'current_debt', original_concept_or_field='LongTermDebtCurrent')
        checkpoint(db, 's0')
        checkpoint(db, 's3', stamp=D+timedelta(microseconds=1))
        checkpoint(db, 's4', endpoints=('unrelated-one','unrelated-two'))
    return paths


def run(paths, decision=D):
    return g.diagnose(research_db=paths[0], production_db=paths[1], decision_at=decision)


def test_exact_roster_and_denominator_reconciliation(tmp_path):
    report = run(fixture(tmp_path))
    r = report['reconciliation']
    assert r['stored_security_id_count']==500 and r['comparable_roster_count']==71
    assert r['roster_counts']==dict(matched=68, ambiguous=2, unmatched=1)
    assert r['outside_comparable_roster_count']==430
    assert r['outside_perimeter_count']==30
    assert r['stored_partition']==dict(matched_roster=68, ambiguous_roster=2,
        outside_perimeter_by_current_classification=30, outside_roster_perimeter_unresolved=400)
    assert sum(r['stored_partition'].values())==500
    assert len(r['roster_details'])==71
    assert not r['approved_eligibility'] and not r['historical_membership_certified']
    assert not r['effective_identity_certified']
    assert all(report['databases'][db]['layers'][layer]['population_denominator']==68
               for db in ('research','production') for layer in ('raw_sec','canonical'))


def test_debt_mismatches_cash_layers_and_completed_retrievals(tmp_path):
    r = run(fixture(tmp_path))
    d = r['databases']['research']
    debt = d['concept_explanations']['current_debt']
    assert debt['all_stored']['concept_mismatch_count']==1322
    assert debt['reference_mismatch_count_delta']==0
    assert debt['all_stored']['original_concepts']['items']==[dict(concept='LongTermDebtCurrent',row_count=1322)]
    assert debt['raw_review_concepts_not_activated']['items']==[dict(concept='LongTermDebtCurrent',row_count=1)]
    cash = d['concept_explanations']['cash']
    assert cash['raw_exact_row_count']==1 and cash['canonical_exact_field_row_count']==0
    assert cash['same_exact_concept_under_other_canonical_fields_row_count']==1
    assert d['raw_canonical_coverage']['cash_and_cash_equivalents']['raw_only']==1
    assert d['layers']['raw_sec']['fields']['current_debt']['adapter_reasons']['concept_mismatch']==1
    assert d['layers']['canonical']['fields']['current_debt']['missing_exact_stored_concept_count']==68
    completed = r['controlled_sec_retrievals']
    assert completed['completed_current_count']==2
    assert completed['completed_by_boundary_count']==1
    assert completed['missing_exact_raw_input_after_completed_retrieval_count']==2
    assert not completed['repeat_retrieval_recommended']
    assert r['estimated_provider_requests']==0 and not r['missing_inputs_are_zero']


def test_period_shapes_endpoints_gaps_overlaps_and_provenance(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        for s,e in [(date(2024,1,1),date(2024,3,31)), (date(2024,5,1),date(2024,7,31)),
                    (date(2024,7,1),date(2024,9,30))]:
            raw(db, 'operating_cash_flow', sid='s3', start=s, end=e, cik='400')
        raw(db, 'capital_expenditure', sid='s3', start=date(2024,1,1),
            end=date(2024,3,30), cik='400')
        canonical(db, 'current_debt', sid='s3', original_concept_or_field='LongTermDebtCurrent',
            public_at=None, source_fact_key=None)
    d = run(paths)['databases']['research']['layers']
    shapes = d['raw_sec']['fields']['capital_expenditure']['stored_period_shapes']
    assert shapes['multi_quarter_cumulative_shape_candidate']==2
    assert shapes['annual_duration_candidate']==1
    pairs = d['raw_sec']['fields']['operating_cash_flow']['period_diagnostics']['adjacent_quarter_pair_counts']
    assert pairs==dict(contiguous=3, gap=1, overlap=1)
    assert d['raw_sec']['fields']['capital_expenditure']['period_diagnostics']['shared_start_multiple_end_count']==1
    value = d['raw_sec']['families']['value']['issue_counts_nonexclusive']
    assert value['companies_with_ocf_periods_without_identical_capex_period']==2
    strength = d['raw_sec']['families']['financial_strength']['issue_counts_nonexclusive']
    assert strength['companies_with_ocf_chain_end_missing_aligned_instants']==1
    # Independent provenance remains visible even behind concept rejection.
    debt = d['canonical']['fields']['current_debt']
    assert debt['adapter_reasons']['concept_mismatch']==1323
    assert debt['independent_provenance_flags']['missing_aware_public_at']==1
    assert debt['independent_provenance_flags']['missing_source_fact_key']==1
    assert d['raw_sec']['families']['value']['certified_formula_count']==0


def test_metadata_projection_read_only_determinism_bounds_and_hashes(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    before = [p.read_bytes() for p in paths]
    original = duckdb.connect
    queries = []
    class Spy:
        def __init__(self, db): self.db=db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self,*args): return self.db.__exit__(*args)
        def execute(self,sql,*args):
            queries.append(sql)
            assert 'SELECT * ' not in sql
            assert '"value"' not in sql and 'payload_json' not in sql and 'model_outputs' not in sql
            return self.db.execute(sql,*args)
    def connect(path, **kwargs):
        assert kwargs['read_only'] is True
        assert kwargs['config']==h._sql_config()
        return Spy(original(path, **kwargs))
    monkeypatch.setattr(duckdb, 'connect', connect)
    r = run(paths)
    assert run(paths)==r and [p.read_bytes() for p in paths]==before
    assert queries and all(fp['before']==fp['after'] for fp in r['database_fingerprints'].values())
    assert r['compact_utf8_bytes']==len(json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8'))
    assert r['compact_utf8_bytes']<=131072
    assert r['unresolved_requirement_count']==len(r['blockers'])==8
    assert all(b['state']=='unresolved' for b in r['blockers'])
    assert all(r[k]==[] for k in PROHIBITED_ARRAYS)
    assert not r['model_executed'] and not r['panel_persisted'] and not r['preregistration_ready']
    assert r['contract_selected'] is None and r['sample_thresholds'] is None and r['validation_credit']==0
    text = json.dumps(r)
    assert 'POISON' not in text and 'DO_NOT_READ' not in text and str(tmp_path) not in text
    samples = r['databases']['research']['layers']['raw_sec']['company_issue_samples']
    assert samples['total_count']==68 and samples['returned_count']==10 and samples['truncated']


def test_boundary_equality_and_future_availability_no_backdating(tmp_path):
    paths = fixture(tmp_path)
    earlier = run(paths,D-timedelta(microseconds=1))
    assert earlier['reconciliation']['comparable_roster_count']==0
    with duckdb.connect(str(paths[0])) as db:
        canonical(db,'capital_expenditure', start=date(2025,1,1),end=date(2025,3,31),
            materialization_run_id='declared', materialized_at=D+timedelta(microseconds=1),
            available_at=D+timedelta(microseconds=1))
    r = run(paths)
    assert r['databases']['research']['layers']['canonical']['fields']['capital_expenditure']['adapter_states']['post_boundary']==1
    assert r['controlled_sec_retrievals']['completed_current_count']==2
    assert r['controlled_sec_retrievals']['completed_by_boundary_count']==1


def test_no_ticker_fallback_duplicates_cik_format_and_class_conflicts(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        add(db,'security_classification_evidence', security_id='s0', security_type='us_operating_company',
            public_at=D,retrieved_at=D,available_at=D,is_current=True,cik='0000000100')
        add(db,'security_classification_evidence', security_id='s5',security_type='bank',
            public_at=D,retrieved_at=D,available_at=D,is_current=True)
        add(db,'security_classification_evidence', security_id='future',security_type='us_operating_company',
            public_at=D,retrieved_at=D+timedelta(seconds=1),available_at=D+timedelta(seconds=1),is_current=True)
    r=run(paths)['reconciliation']
    assert r['comparable_roster_count']==71
    assert r['roster_counts']==dict(matched=67,ambiguous=3,unmatched=1)
    assert len(r['roster_details'])==71


def test_unknown_or_conflicting_exclusion_types_remain_unresolved():
    data={table:[] for table in g.SOURCES}
    data['security_listings']=[{'security_id':'unknown'},{'security_id':'conflict'}]
    for sid,kind in [('unknown','invented_type'),('conflict','bank'),('conflict','investment_fund')]:
        data['security_classification_evidence'].append(dict(security_id=sid,security_type=kind,
            public_at=D,retrieved_at=D,available_at=D,is_current=True))
    r,matched,_=g._reconcile(data,D)
    assert r['outside_perimeter_count']==0 and not matched
    assert r['stored_partition']['outside_roster_perimeter_unresolved']==2


def test_unsupported_views_do_not_become_missing_or_zero(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:
        db.execute('DROP TABLE sec_facts')
        db.execute("CREATE VIEW sec_facts AS SELECT error('DO NOT EVALUATE') AS concept")
    r=run(paths)['databases']['production']
    assert not r['layers']['raw_sec']['adapter_supported']
    assert r['layers']['raw_sec']['fields']['current_debt']['missing_exact_stored_concept_count'] is None
    assert r['raw_canonical_coverage']['current_debt']['neither'] is None


@pytest.mark.parametrize('bound', ['MAX_ROWS','MAX_CELL_CHARS','MAX_ROSTER','MAX_PERIOD_WORK','MAXIMUM_BYTES'])
def test_resource_bounds_fail_closed_with_after_hashes(tmp_path, monkeypatch, bound):
    paths=fixture(tmp_path)
    fingerprints=[]
    original=h.fingerprint
    def fingerprint(path): fingerprints.append(path); return original(path)
    monkeypatch.setattr(h,'fingerprint',fingerprint)
    monkeypatch.setattr(g,bound,1)
    with pytest.raises(h.InvestmentResearchError): run(paths)
    assert fingerprints==[paths[0],paths[1],paths[0],paths[1]]


def test_failure_attempts_both_after_hashes_and_detects_change(tmp_path, monkeypatch):
    paths=fixture(tmp_path); calls=[]; original=h.fingerprint
    def fingerprint(path):
        calls.append(path)
        if len(calls)==3: raise OSError('private path')
        return original(path)
    monkeypatch.setattr(h,'fingerprint',fingerprint)
    with pytest.raises(g.GapDiagnosticError): run(paths)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]
    calls.clear()
    def changed(path):
        calls.append(path)
        result=original(path)
        if len(calls)==3: result['sha256']='changed'
        return result
    monkeypatch.setattr(h,'fingerprint',changed)
    with pytest.raises(g.GapDiagnosticError): run(paths)


def test_cli_success_and_redacted_nonzero_failure(tmp_path):
    paths=fixture(tmp_path)
    command=[sys.executable,'-m','app.investment_research_cli','track-b-identity-accounting-gap-diagnostic',
        '--research-db',str(paths[0]),'--production-db',str(paths[1]),'--decision-at',D.isoformat()]
    success=subprocess.run(command,capture_output=True,text=True)
    assert success.returncode==0 and json.loads(success.stdout)==run(paths)
    command[-1]=D.replace(tzinfo=None).isoformat()
    failure=subprocess.run(command,capture_output=True,text=True)
    assert failure.returncode==1 and not failure.stdout
    assert str(tmp_path) not in failure.stderr and 'Traceback' not in failure.stderr
    assert json.loads(failure.stderr)['error']['code']=='INVESTMENT_RESEARCH_NOT_READY'


def test_operation_identity_mismatch_does_not_establish_completion(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute("UPDATE sec_liquidity_raw_provenance SET concept_contract_hash='other' WHERE security_id='s0'")
        db.execute("UPDATE sec_liquidity_checkpoints SET transaction_succeeded=false WHERE security_id='s3'")
    r=run(paths)['controlled_sec_retrievals']
    assert r['completed_current_count']==0 and not r['repeat_retrieval_recommended']


def test_complete_shapes_remain_unverified_and_layers_never_substitute(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        for start,end in QUARTERS:
            raw(db,'capital_expenditure',start=start,end=end)
        for field in ('current_debt','non_current_debt'):
            raw(db,field)
    r=run(paths)['databases']['research']['layers']
    for family in ('value','financial_strength'):
        assert r['raw_sec']['families'][family]['issue_counts_nonexclusive']['structural_chain_present_unverified']==1
        assert r['raw_sec']['families'][family]['certified_formula_count']==0
        assert r['canonical']['families'][family]['issue_counts_nonexclusive']['no_structural_chain']==68


def test_baseline_failure_attempts_both_baseline_and_after_hashes(tmp_path, monkeypatch):
    paths=fixture(tmp_path); calls=[]; original=h.fingerprint
    def fingerprint(path):
        calls.append(path)
        if len(calls)==1: raise OSError('private path')
        return original(path)
    monkeypatch.setattr(h,'fingerprint',fingerprint)
    with pytest.raises(g.GapDiagnosticError): run(paths)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]


@pytest.mark.parametrize('fail_diagnostic',[False,True])
def test_complete_operator_python_portion_saves_utf8_and_checks_finally(tmp_path, monkeypatch, capsys, fail_diagnostic):
    paths=fixture(tmp_path)
    script=Path(__file__).resolve().parents[2]/'scripts'/'track-b-gaps-verify.ps1'
    code=script.read_text(encoding='utf-8').split("$Verify = @'\n",1)[1].split("\n'@",1)[0]
    compile(code,str(script),'exec')
    reports=tmp_path/'reports'
    monkeypatch.setattr(sys,'argv',['verify',str(paths[0]),str(paths[1]),str(reports)])
    original=subprocess.run
    invocations=[]
    def controlled(command, **kwargs):
        invocations.append(command)
        if command[2]=='pytest':
            assert all(Path(t).exists() for t in command[3:-1])
            return subprocess.CompletedProcess(command,0,stdout=b'offline tests already tested',stderr=b'')
        if fail_diagnostic:
            return subprocess.CompletedProcess(command,1,stdout=b'',stderr=b'PRIVATE_FAILURE')
        return original(command,**kwargs)
    monkeypatch.setattr(subprocess,'run',controlled)
    before=[p.read_bytes() for p in paths]
    with pytest.raises(SystemExit) as result:
        exec(code,{})
    assert result.value.code==int(fail_diagnostic)
    assert [p.read_bytes() for p in paths]==before
    summary=json.loads((reports/'track-b-gaps-verification.json').read_text(encoding='utf-8'))
    assert summary['failed']==fail_diagnostic
    assert sum(e['reason_code']=='FINGERPRINT_UNCHANGED' for e in summary['events'])==2
    assert 'PRIVATE_FAILURE' not in capsys.readouterr().out
    if not fail_diagnostic:
        assert sum(e['reason_code']=='DIAGNOSTIC_VERIFIED' for e in summary['events'])==2
        for index in range(2):
            payload=(reports/f'track-b-gaps-{index}.json').read_bytes()
            assert not payload.startswith(b'\xef\xbb\xbf')
            assert len(payload)<=131072
            assert json.loads(payload)['compact_utf8_bytes']==len(payload)
    else:
        assert not list(reports.glob('track-b-gaps-[01].json'))
