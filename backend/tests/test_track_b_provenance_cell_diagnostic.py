"""Offline regression for safe length diagnostics; operator cause remains unmeasured."""
import hashlib
import json
from pathlib import Path
import duckdb
import pytest
from app import track_b_provenance_replay as r
from test_track_b_provenance_replay import databases,run,fixture


def diagnostic_runner():
    path=Path(__file__).parents[2]/'scripts/track-b-provenance-cell-diagnose.ps1'
    source=path.read_text(encoding='utf-8')
    payload=source.split("$Diagnostic = @'\n",1)[1].split("\n'@",1)[0]
    ns={'__name__':'offline_diagnostic_test'};exec(compile(payload,str(path),'exec'),ns)
    return ns,source


def test_operator_section_shape_locates_first_raw_read_and_redacts_cell(tmp_path):
    paths=databases(tmp_path);rejected='REJECTED_SECRET_'+'é'*1100
    with duckdb.connect(str(paths[0])) as db:db.execute('UPDATE sec_facts SET frame=?',[rejected])
    report=run(paths)
    assert report['execution_state']=='failed' and report['errors']==['METADATA_CELL_LIMIT']
    assert report['mapping_audits']=={} and report['source_schemas']=={}
    diagnostic=report['safe_read_diagnostic'];column=diagnostic['offending_columns'][0]
    assert diagnostic['read_stage']=='research.sec_facts' and diagnostic['table']=='sec_facts'
    assert column['source_column']==column['projected_column']=='frame'
    assert column['rejected_cell_count']==2 and column['max_characters']==len(rejected)
    assert column['cell_lengths'][0]==dict(characters=len(rejected),utf8_bytes=len(rejected.encode()))
    assert b'REJECTED_SECRET' not in r.encode(report)
    assert all(x['unchanged'] for x in report['database_hashes'].values())


def test_full_long_opaque_identity_is_hashed_in_sql_and_replay_is_not_rejected(tmp_path):
    paths=databases(tmp_path);token='OPAQUE_SECRET_'+'é'*2048
    with duckdb.connect(str(paths[0])) as db:
        db.execute('UPDATE sec_facts SET ingestion_plan_id=?',[token])
        db.execute('UPDATE sec_liquidity_raw_provenance SET plan_id=?',[token])
        db.execute('UPDATE sec_liquidity_runs SET plan_id=?',[token])
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        original=r.cell_diagnostics(db,'sec_facts',r.FACT_FIELDS,'research.sec_facts',original_projection=True)
        assert original['offending_columns'][0]['projected_column']=='ingestion_plan_id'
        assert original['offending_columns'][0]['max_characters']==len(token)
        _,rows=r.metadata_rows(db,'sec_facts',r.FACT_FIELDS,r.FACT_REQUIRED,r.MAX_ROWS)
        assert all('ingestion_plan_id' not in row for row in rows)
        assert all(row['ingestion_plan_id_sha256']==hashlib.sha256(token.encode()).hexdigest() for row in rows)
    report=run(paths)
    assert report['execution_state']=='completed',report
    assert report['pilot']['state']=='selected'
    assert b'OPAQUE_SECRET' not in r.encode(report)
    assert r.MAX_CELL==1024 and r.MAX_PAYLOAD==5000000 and r.MAX_TOTAL_PAYLOAD==10000000
    assert r.MAX_ACCESSIONS==3 and r.MAX_OBSERVATIONS==24 and r.MAX_REPORT==131072


def test_shared_long_prefix_does_not_establish_plan_identity(tmp_path):
    paths=databases(tmp_path);prefix='PREFIX_'+'x'*3000
    with duckdb.connect(str(paths[0])) as db:
        db.execute('UPDATE sec_facts SET ingestion_plan_id=?',[prefix+'one'])
        db.execute('UPDATE sec_liquidity_raw_provenance SET plan_id=?',[prefix+'one'])
        db.execute('UPDATE sec_liquidity_runs SET plan_id=?',[prefix+'two'])
    report=run(paths)
    assert report['errors']==['RUN_LINEAGE_MISMATCH']
    assert b'PREFIX_' not in r.encode(report)


def test_diagnostic_exact_original_projection_and_redacted_unicode_lengths(tmp_path):
    paths=databases(tmp_path);token='DIAGNOSTIC_SECRET_'+('é'*1500)
    with duckdb.connect(str(paths[0])) as db:db.execute('UPDATE sec_facts SET ingestion_plan_id=?',[token])
    ns,source=diagnostic_runner()
    assert ns['FIELDS']==tuple(dict.fromkeys(r.FACT_FIELDS+r.feasibility.OPTIONAL))
    result=ns['diagnose'](dict(zip(('research','production'),paths)),tmp_path/'reports')
    assert result['execution_state']=='completed' and result['operator_replay_succeeded'] is False
    findings=result['diagnostic']['offending_columns']
    assert len(findings)==1 and findings[0]['projected_column']=='ingestion_plan_id'
    assert findings[0]['cell_lengths'][0]==dict(characters=len(token),utf8_bytes=len(token.encode()))
    raw=(tmp_path/'reports'/'track-b-provenance-cell-diagnostic.json').read_bytes()
    assert b'DIAGNOSTIC_SECRET' not in raw and not raw.startswith(b'\xef\xbb\xbf')
    assert all(v['unchanged'] for v in result['database_hashes'].values())
    assert '-X utf8 -' in source and 'pytest' not in ns['inspect'].__code__.co_names


def test_diagnostic_samples_bounded_and_counts_exact(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('DELETE FROM sec_facts')
        db.execute('INSERT INTO sec_facts(fact_key,security_id,cik,taxonomy,concept,value,unit,frame) SELECT CAST(i AS VARCHAR),\'s\',\'100\',\'us-gaap\',\'c\',1,\'USD\',repeat(\'x\',1100+CAST(i AS INTEGER)) FROM range(12) t(i)')
    ns,_=diagnostic_runner();report=ns['diagnose'](dict(zip(('research','production'),paths)),tmp_path/'reports')
    finding=report['diagnostic']['offending_columns'][0]
    assert finding['rejected_cell_count']==12 and len(finding['cell_lengths'])==8
    assert finding['sample_truncated'] and finding['min_characters']==1100 and finding['max_characters']==1111


def test_diagnostic_failed_pre_hash_still_attempts_both_post_hashes(tmp_path):
    paths=databases(tmp_path);missing=tmp_path/'missing.duckdb';ns,_=diagnostic_runner();original=ns['fingerprint'];calls=[]
    def fingerprint(path):calls.append(path);return original(path)
    ns['fingerprint']=fingerprint
    report=ns['diagnose']({'research':missing,'production':paths[1]},tmp_path/'reports')
    assert calls==[missing,paths[1],missing,paths[1]]
    assert report['execution_state']=='failed' and report['database_hashes']['production']['unchanged']
    assert (tmp_path/'reports'/'track-b-provenance-cell-diagnostic.json').exists()


def test_payload_projection_cannot_use_descriptive_path(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='PAYLOAD_IN_METADATA_PROJECTION'):
            r.metadata_rows(db,r.RAW_TABLE,('payload_json',),set(),r.MAX_ROWS)


def test_descriptive_limit_never_relaxed_even_with_valid_opaque_refs(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('UPDATE sec_facts SET frame=?,ingestion_plan_id=?',['x'*1025,'p'*2000])
    report=run(paths)
    assert report['errors']==['METADATA_CELL_LIMIT']
    columns=report['safe_read_diagnostic']['offending_columns']
    assert [c['source_column'] for c in columns]==['frame']


def test_null_and_empty_capabilities_are_not_hashed_into_valid_identity(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute("UPDATE sec_facts SET ingestion_plan_id='' WHERE fact_key='0'")
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        _,rows=r.metadata_rows(db,'sec_facts',r.FACT_FIELDS,r.FACT_REQUIRED,r.MAX_ROWS)
    assert next(row for row in rows if row['fact_key']=='0')['ingestion_plan_id_sha256'] is None


def test_selected_run_metadata_remains_inside_total_budget(tmp_path):
    paths=databases(tmp_path);pair,_,_=fixture()
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        with pytest.raises(r.ReplayError,match='METADATA_ROW_LIMIT'):r.verify_lineage(db,pair,remaining=0)


def test_unrelated_run_capabilities_never_enter_selected_run_projection(tmp_path):
    paths=databases(tmp_path);pair,_,_=fixture()
    with duckdb.connect(str(paths[0])) as db:
        db.execute("INSERT INTO sec_liquidity_runs SELECT 'unrelated',operation_type,operation_contract_version,concept_contract_hash,lineage_id,plan_id,decision_at,started_at,finished_at,status,request_budget,request_count,inserted_count,unchanged_count,stop_reason,production_sha256_before,production_sha256_after FROM sec_liquidity_runs")
        db.execute('UPDATE sec_liquidity_runs SET operation_contract_version=? WHERE run_id=\'unrelated\'',['x'*2000])
    report=run(paths)
    assert report['execution_state']=='completed',report
