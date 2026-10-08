"""Synthetic offline fixtures only; never operator/provider evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import duckdb
import pytest
from app import track_b_provenance_replay as r
from app import sec_liquidity_ingestion as ingestion
D=datetime(2026,10,5,0,30,tzinfo=timezone.utc)
OLD=datetime(2026,8,1,tzinfo=timezone.utc)
ACC='0000000100-26-000001'


def fixture():
    identity=dict(operation_type=r.OPERATION_TYPE,operation_contract_version=r.OPERATION_CONTRACT_VERSION,
        concept_contract_hash=r.CONCEPT_CONTRACT_HASH,lineage_id=ingestion._lineage_id(D.isoformat()),
        run_id='run',plan_id='plan',security_id='s',cik='0000000100')
    company={'cik':100,'facts':{'us-gaap':{r.feasibility.OCF:{'units':{'USD':[
        dict(start='2025-01-01',end=end,accn=ACC,val=i+100,fy=2025,fp='Q'+str(i+1),form='10-Q',filed='2026-07-31')
        for i,end in enumerate(['2025-03-31','2025-06-30'])]}}}}}
    submissions={'cik':'0000000100','filings':{'recent':{'accessionNumber':[ACC],'form':['10-Q'],
        'filingDate':['2026-07-31'],'acceptanceDateTime':['2026-07-31T12:00:00Z'],'primaryDocument':['never-follow.htm']},
        'files':[{'name':'never-follow.json'}]}}
    pair=[];texts=[]
    for kind,payload in [('companyfacts',company),('submissions',submissions)]:
        text=json.dumps(payload,ensure_ascii=False,separators=(',',':')); digest=hashlib.sha256(text.encode()).hexdigest()
        key=hashlib.sha256(f"{identity['lineage_id']}|0000000100|{kind}|{digest}".encode()).hexdigest()
        pair.append(dict(identity,evidence_key=key,endpoint_class=kind,retrieved_at=OLD,response_sha256=digest,
            byte_count=len(text.encode()),content_type='application/json',parser_version=r.PARSER_VERSION));texts.append(text)
    rows=[dict(fact_key=str(i),security_id='s',cik='0000000100',taxonomy='us-gaap',concept=r.feasibility.OCF,
        unit='USD',period_start='2025-01-01',period_end=end,accession_number=ACC,
        public_at=datetime(2026,7,31,12,tzinfo=timezone.utc),retrieved_at=OLD,fiscal_year=2025,
        fiscal_period='Q'+str(i+1),frame=None,form='10-Q',filed_date='2026-07-31',is_amendment=False,is_revision=False,
        source_endpoint='companyfacts',parser_contract_version=r.PARSER_VERSION,operation_type=r.OPERATION_TYPE,
        operation_contract_version=r.OPERATION_CONTRACT_VERSION,concept_contract_hash=r.CONCEPT_CONTRACT_HASH,
        ingestion_run_id='run',ingestion_plan_id='plan') for i,end in enumerate(['2025-03-31','2025-06-30'])]
    return pair,texts,rows


def parsed():
    pair,texts,rows=fixture()
    return [r.verify_payload(p,t)[0] for p,t in zip(pair,texts)],rows


def databases(tmp_path):
    pair,texts,rows=fixture();research=tmp_path/'research.duckdb';production=tmp_path/'production.duckdb'
    with duckdb.connect(str(research)) as db:
        db.execute(ingestion.SCHEMA)
        db.execute('CREATE TABLE security_classification_evidence(security_id VARCHAR,security_type VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ)')
        db.execute("INSERT INTO security_classification_evidence VALUES ('s','us_operating_company',?,?,?)",[OLD]*3)
        db.execute("INSERT INTO sec_issuers(security_id,qualified_symbol,ticker,cik) VALUES ('s','S.US','S','0000000100')")
        for row in rows:
            fields=list(row)+['value'];db.execute('INSERT INTO sec_facts('+','.join(fields)+') VALUES ('+','.join(['?']*len(fields))+')',list(row.values())+[123])
        for item,text in zip(pair,texts):
            fields=list(item)+['payload_json'];db.execute('INSERT INTO '+r.RAW_TABLE+'('+','.join(fields)+') VALUES ('+','.join(['?']*len(fields))+')',list(item.values())+[text])
        db.execute('INSERT INTO sec_liquidity_runs(run_id,operation_type,operation_contract_version,concept_contract_hash,lineage_id,plan_id,decision_at,started_at,status,request_budget,production_sha256_before) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ['run',r.OPERATION_TYPE,r.OPERATION_CONTRACT_VERSION,r.CONCEPT_CONTRACT_HASH,pair[0]['lineage_id'],'plan',D,OLD,'completed',2,'synthetic'])
    with duckdb.connect(str(production)) as db:db.execute('CREATE TABLE unrelated(x INTEGER)')
    return research,production


def run(paths):return r.run(research_db=paths[0],production_db=paths[1],decision_at=D.isoformat())


def test_original_utf8_not_reserialized():
    pair,texts,_=fixture()
    for item,text in zip(pair,texts):
        assert r.verify_payload(item,text)[1]['hash_and_size_verified']
        with pytest.raises(r.ReplayError,match='HASH|BYTE_COUNT'):r.verify_payload(item,json.dumps(json.loads(text),indent=2))


@pytest.mark.parametrize('field,value,code',[('byte_count',1,'BYTE_COUNT'),('response_sha256','bad','HASH'),
    ('evidence_key','bad','EVIDENCE_KEY'),('content_type','text/html','CONTENT_TYPE')])
def test_integrity_refusals(field,value,code):
    pair,texts,_=fixture();pair[0][field]=value
    with pytest.raises(r.ReplayError,match=code):r.verify_payload(pair[0],texts[0])


def test_selection_value_and_order_independent():
    pair,_,rows=fixture();selection,info=r.select_pilot(pair,rows,D)
    changed=[dict(row,value='NEVER_EXAMINE') for row in rows]
    other,alternate=r.select_pilot(list(reversed(pair)),list(reversed(changed)),D)
    assert info==alternate and [x['fact_key'] for x in selection[1]]==[x['fact_key'] for x in other[1]]


def test_no_pair_ambiguity_and_no_pattern():
    pair,_,rows=fixture()
    assert r.select_pilot(pair[:1],rows,D)[1]['state']=='no_qualifying_pair'
    assert r.select_pilot(pair+[deepcopy(pair[0])],rows,D)[1]['skipped']['ambiguous_retained_pair']==1
    assert r.select_pilot(pair,[dict(rows[0],frame='CY2025Q1')],D)[0] is None


def test_late_and_oversize_not_selected():
    pair,_,rows=fixture();pair[0]['retrieved_at']=datetime(2027,1,1,tzinfo=timezone.utc)
    assert r.select_pilot(pair,rows,D)[0] is None
    pair[0]['retrieved_at']=OLD;pair[0]['byte_count']=r.MAX_PAYLOAD+1
    assert r.select_pilot(pair,rows,D)[0] is None


def test_duplicate_multiplicity_and_no_revision_inference():
    (company,submissions),rows=parsed();items=company['facts']['us-gaap'][r.feasibility.OCF]['units']['USD']
    items.append(dict(items[0],val='999999'))
    for key in submissions['filings']['recent']:submissions['filings']['recent'][key]*=2
    report=r.replay(company,submissions,rows+[dict(rows[0],fact_key='duplicate',fiscal_year=None)])
    recovered=report['metadata_recovered_from_verified_payloads'];counts=recovered['counts']
    assert counts['payload_observations_replayed']==3 and counts['source_metadata_match_edges']==5 and counts['submission_match_edges']==10
    assert recovered['comparisons_by_field']['fiscal_year']['missing_stored_recoverable']==2
    assert not report['conflicting_amendments_inferred'] and not report['supersession_inferred']
    assert b'999999' not in r.encode(report) and b'2025-03-31' not in r.encode(report)


def test_metadata_difference_not_replacement():
    (company,submissions),rows=parsed();rows[0]['fiscal_period']='FY'
    report=r.replay(company,submissions,rows)
    assert report['metadata_recovered_from_verified_payloads']['comparisons_by_field']['fiscal_period']['different_metadata']==1
    assert rows[0]['fiscal_period']=='FY'


def test_duplicates_never_truncated_to_fit():
    (company,submissions),rows=parsed();items=company['facts']['us-gaap'][r.feasibility.OCF]['units']['USD'];items[:]=[items[0]]*25
    with pytest.raises(r.ReplayError,match='REPLAY_OBSERVATION_LIMIT'):r.replay(company,submissions,rows)
    pair,_,_=fixture()
    with pytest.raises(r.ReplayError,match='STORED_MATCH_MULTIPLICITY_LIMIT'):
        r.select_pilot(pair,[dict(rows[0],fact_key=str(i)) for i in range(25)],D)


def test_three_accessions_24_rows():
    pair,_,rows=fixture();rows=[dict(rows[0],accession_number=f'acc-{i}',fact_key=f'{i}-{j}') for i in range(4) for j in range(8)]
    selection,info=r.select_pilot(pair,rows,D)
    assert info['selected_stored_rows']==24 and len(info['accessions'])==3 and info['excluded_stored_rows']==8


def test_array_lengths_not_zip_truncated():
    (company,submissions),rows=parsed();submissions['filings']['recent']['form']=[]
    with pytest.raises(r.ReplayError,match='ARRAY_LENGTH_MISMATCH'):r.replay(company,submissions,rows)


def test_labels_and_flags_never_prove_context_calendar_precision_revisions():
    _,_,rows=fixture();report=r.mapping_audit(rows,r.FACT_FIELDS,D)
    assert report['metadata_already_recognized']['accepted_proof_rows']==dict(fiscal=0,context=0,revision=0,precision=0)
    assert report['stored_metadata_needing_interpretation']['is_revision']['present_rows']==2


def test_extra_companyfacts_fields_do_not_manufacture_proofs():
    (company,submissions),rows=parsed()
    company['facts']['us-gaap'][r.feasibility.OCF]['units']['USD'][0].update(decimals='0',contextRef='invented',revision_status='superseded')
    fields=r.replay(company,submissions,rows)['metadata_recovered_from_verified_payloads']['comparisons_by_field']
    assert not {'decimals','contextRef','revision_status'}&set(fields)


def test_missing_submissions_stays_unknown_no_url_follow():
    (company,submissions),rows=parsed()
    for key in submissions['filings']['recent']:submissions['filings']['recent'][key]=[]
    report=r.replay(company,submissions,rows)
    assert report['metadata_recovered_from_verified_payloads']['counts']['replayed_observations_without_recent_submission_match']==2
    assert not report['filings_files_references_followed']


def test_complete_readonly_run_and_all_blockers(tmp_path):
    paths=databases(tmp_path);before=[r.fingerprint(p) for p in paths];report=run(paths)
    assert report['execution_state']=='completed',report
    assert report['pilot']['state']=='selected',report
    assert report['pilot']['replay']['metadata_recovered_from_verified_payloads']['counts']['payload_observations_replayed']==2
    assert before==[r.fingerprint(p) for p in paths] and all(x['unchanged'] for x in report['database_hashes'].values())
    assert len(report['blockers'])==8 and all(x['state']=='unresolved' for x in report['blockers'])
    assert len(r.encode(report))<=r.MAX_REPORT


@pytest.mark.parametrize('sql,code',[("UPDATE sec_liquidity_raw_provenance SET response_sha256='bad' WHERE endpoint_class='companyfacts'",'PAYLOAD_HASH_MISMATCH'),
    ("UPDATE sec_liquidity_runs SET plan_id='wrong'",'RUN_LINEAGE_MISMATCH')])
def test_lineage_and_corruption_fail_closed_with_hashes(tmp_path,sql,code):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute(sql)
    report=run(paths)
    assert report['execution_state']=='failed' and code in report['errors']
    assert all(x['unchanged'] for x in report['database_hashes'].values())


def test_independent_hash_attempts_even_missing_input(tmp_path,monkeypatch):
    paths=databases(tmp_path);missing=tmp_path/'missing.duckdb';calls=[];original=r.fingerprint
    def instrument(path):calls.append(Path(path));return original(path)
    monkeypatch.setattr(r,'fingerprint',instrument)
    report=run((missing,paths[1]))
    assert calls==[missing,paths[1],missing,paths[1]] and report['database_hashes']['production']['unchanged']


def test_actual_size_checked_before_payload_projection(tmp_path,monkeypatch):
    paths=databases(tmp_path);pair,_,_=fixture();monkeypatch.setattr(r,'MAX_PAYLOAD',20)
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='ACTUAL_PAYLOAD_SIZE'):r.retained_payload(db,pair[0])


def test_output_bound_retains_failure_hashes(tmp_path,monkeypatch):
    paths=databases(tmp_path);monkeypatch.setattr(r,'MAX_REPORT',4000);report=run(paths)
    assert report['errors']==['REPORT_SIZE_LIMIT'] and 'mapping_audits' not in report and len(r.encode(report))<4000


def test_metadata_projections_exclude_values_and_payload(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        _,rows=r.metadata_rows(db,'sec_facts',r.FACT_FIELDS,r.FACT_REQUIRED,r.MAX_ROWS)
        assert all('value' not in row for row in rows)
        _,manifest=r.metadata_rows(db,r.RAW_TABLE,r.MANIFEST,set(r.MANIFEST)|{'payload_json'},r.MAX_MANIFEST)
        assert all('payload_json' not in row for row in manifest)


def test_issuer_lineage_refused_even_if_called_without_perimeter_filter(tmp_path):
    paths=databases(tmp_path);pair,_,_=fixture()
    with duckdb.connect(str(paths[0])) as db:db.execute("UPDATE sec_issuers SET cik='0000000101'")
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='ISSUER_LINEAGE_UNPROVEN'):r.verify_lineage(db,pair)


def test_utf8_unicode_byte_count_and_payload_cik_mismatch():
    pair,_,_=fixture();item=pair[0]
    def prepare(text):
        item['byte_count']=len(text.encode('utf-8'));item['response_sha256']=hashlib.sha256(text.encode()).hexdigest()
        item['evidence_key']=hashlib.sha256(f"{item['lineage_id']}|{item['cik']}|{item['endpoint_class']}|{item['response_sha256']}".encode()).hexdigest()
    text='{"cik":100,"name":"é"}';prepare(text)
    assert r.verify_payload(item,text)[1]['verified_byte_count']>len(text)
    text='{"cik":101}';prepare(text)
    with pytest.raises(r.ReplayError,match='PAYLOAD_CIK_MISMATCH'):r.verify_payload(item,text)
    text='{"cik":100,"cik":101}';prepare(text)
    with pytest.raises(r.ReplayError,match='DUPLICATE_JSON_MEMBER'):r.verify_payload(item,text)


def test_proof_columns_present_but_post_boundary_remain_unaccepted():
    from test_track_b_construction_feasibility import row
    complete=row();complete['fiscal_metadata_available_at']=datetime(2027,1,1,tzinfo=timezone.utc)
    report=r.mapping_audit([complete],list(complete),D)
    assert report['metadata_already_recognized']['accepted_proof_rows']['fiscal']==0
    assert report['stored_proof_field_inventory']['fiscal_year_start']['present_rows']==1
    assert report['unaccepted_proof_rows']['fiscal']==1


def test_submission_duplicates_bounded_without_truncation():
    (company,submissions),rows=parsed()
    for key in submissions['filings']['recent']:submissions['filings']['recent'][key]*=25
    with pytest.raises(r.ReplayError,match='SUBMISSION_MATCH_MULTIPLICITY_LIMIT'):r.replay(company,submissions,rows)


def test_pair_security_operation_parser_links_required():
    pair,_,rows=fixture()
    for key in ('security_id','cik','operation_type','ingestion_run_id','ingestion_plan_id','parser_contract_version'):
        altered=[dict(row,**{key:'wrong'}) for row in rows]
        assert r.select_pilot(pair,altered,D)[0] is None


def test_original_pair_retrieval_time_matches(tmp_path):
    paths=databases(tmp_path);pair,_,_=fixture();pair[1]['retrieved_at']=D
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='PAIR_RETRIEVAL_MISMATCH'):r.verify_lineage(db,pair)


def test_no_fallback_after_selected_corrupt_pair(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute("UPDATE sec_liquidity_raw_provenance SET response_sha256='bad' WHERE endpoint_class='submissions'")
    report=run(paths)
    assert report['errors']==['PAYLOAD_HASH_MISMATCH']
    assert len(report['pilot']['payload_checks'])==1


def test_schema_omission_not_counted_as_no_pair(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute('ALTER TABLE sec_liquidity_raw_provenance DROP COLUMN parser_version')
    report=run(paths)
    assert report['pilot']['state']=='unproven_source_schema'
    assert report['retained_manifest']['state']=='unsupported_schema'


def test_raw_available_at_does_not_override_established_visibility():
    _,_,rows=fixture();rows[0]['available_at']=datetime(2027,1,1,tzinfo=timezone.utc)
    counts,found=r.visible(rows,{'s'},D)
    assert counts['visible_relevant_rows']==2
    rows[0]['retrieved_at']=datetime(2027,1,1,tzinfo=timezone.utc)
    counts,found=r.visible(rows,{'s'},D)
    assert counts['visible_relevant_rows']==1 and counts['post_decision_rows']==1


def test_metadata_rows_limit_before_projection(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='METADATA_ROW_LIMIT'):r.metadata_rows(db,'sec_facts',r.FACT_FIELDS,r.FACT_REQUIRED,1)


def test_empty_retained_manifest_reports_no_qualifying_pair(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute('DELETE FROM sec_liquidity_raw_provenance')
    report=run(paths)
    assert report['execution_state']=='completed' and report['pilot']['state']=='no_qualifying_pair'
    assert all(x['unchanged'] for x in report['database_hashes'].values())


def test_changed_database_hash_invalidates_even_successful_replay(tmp_path,monkeypatch):
    paths=databases(tmp_path);original=r.fingerprint;calls=0
    def altered(path):
        nonlocal calls
        calls+=1;result=original(path)
        if calls==3:result['sha256']='0'*64
        return result
    monkeypatch.setattr(r,'fingerprint',altered);report=run(paths)
    assert report['execution_state']=='failed' and 'HASH_NOT_VERIFIED_RESEARCH' in report['errors']
    assert report['database_hashes']['production']['unchanged']


def test_replay_never_requests_network(tmp_path,monkeypatch):
    import socket
    paths=databases(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('NETWORK_REQUEST_FORBIDDEN')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    report=run(paths)
    assert report['execution_state']=='completed' and report['provider_requests']==0
