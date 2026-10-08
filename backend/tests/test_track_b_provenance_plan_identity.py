"""Confirmed operator shape reproduced only with synthetic offline databases."""
import hashlib
import json
from pathlib import Path
import duckdb
import pytest
from app import track_b_gaps as g
from app import track_b_provenance_replay as r
from test_track_b_provenance_replay import databases,run,D
from test_track_b_provenance_verification import runner,simulate,verify

PREFIX='FULL_LENGTH_SYNTHETIC_PLAN:'
TOKEN=PREFIX+'x'*(11171-len(PREFIX))
TABLES=('sec_facts','sec_liquidity_runs','sec_liquidity_checkpoints','sec_liquidity_raw_provenance')


def full_length_databases(tmp_path):
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        for table in TABLES:
            column='ingestion_plan_id' if table=='sec_facts' else 'plan_id'
            db.execute(f'UPDATE {table} SET {column}=?',[TOKEN])
    return paths


def different_end():
    changed=TOKEN[:-1]+'y'
    assert len(changed)==11171 and changed[:11170]==TOKEN[:11170]
    return changed


def test_exact_confirmed_count_and_length_legacy_guard_then_full_reference(tmp_path):
    # The operator count/length is reproduced; fixture values are invented.
    with duckdb.connect(str(tmp_path/'synthetic-count.duckdb'),config=r.history._sql_config()) as db:
        # A generated constant on a base table reproduces the exact count/length
        # without a 484 MB duplicate fixture. Stored-token matching is separately
        # tested in all four physical ingestion tables below, with the same cap.
        token_literal="'"+TOKEN.replace("'","''")+"'"
        db.execute('CREATE TABLE sec_facts(fixture_row INTEGER, ingestion_plan_id VARCHAR GENERATED ALWAYS AS ('+token_literal+') VIRTUAL)')
        db.execute('INSERT INTO sec_facts(fixture_row) SELECT i FROM range(43369) t(i)')
        old=db.execute('SELECT count(*),min(length(ingestion_plan_id)),max(length(ingestion_plan_id)),min(octet_length(encode(ingestion_plan_id))) FROM sec_facts WHERE length(ingestion_plan_id)>1024').fetchone()
        assert old==(43369,11171,11171,11171)
        diagnostic=r.cell_diagnostics(db,'sec_facts',('ingestion_plan_id',),'research.sec_facts',original_projection=True)
        finding=diagnostic['offending_columns'][0]
        assert finding['rejected_cell_count']==43369 and len(finding['cell_lengths'])==8
        _,rows=r.metadata_rows(db,'sec_facts',('ingestion_plan_id',),{'ingestion_plan_id'},r.MAX_ROWS)
        assert len(rows)==43369
        expected='sha256:'+hashlib.sha256(TOKEN.encode('utf-8')).hexdigest()
        assert {row['ingestion_plan_id_sha256'] for row in rows}=={expected}
        assert all('ingestion_plan_id' not in row for row in rows)
        assert PREFIX not in json.dumps(diagnostic)


def test_all_four_sources_match_pr95_representation_and_no_raw_token_projection(tmp_path):
    paths=full_length_databases(tmp_path)
    expected='sha256:'+hashlib.sha256(TOKEN.encode()).hexdigest()
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        for table in TABLES:
            column='ingestion_plan_id' if table=='sec_facts' else 'plan_id'
            alias=r.OPAQUE_REFERENCES[column]
            _,rows=r.metadata_rows(db,table,(column,),{column},r.MAX_ROWS)
            assert rows and all(row=={alias:expected} for row in rows)
            assert all(column not in row for row in rows)
        pr95=db.execute('SELECT '+g._projection_expression('sec_liquidity_runs','plan_id')+' FROM sec_liquidity_runs').fetchone()[0]
        assert pr95==expected
    report=run(paths)
    assert report['execution_state']=='completed',report
    checks=report['pilot']['lineage_checks']
    assert all(checks[key] is True for key in ('pair_identity_verified','issuer_mapping_verified',
        'run_identity_and_decision_lineage_verified','source_fact_operation_links_verified',
        'checkpoint_full_identity_verified','checkpoint_completion_visible'))
    assert checks['selected_lineage_metadata_rows']==2
    assert report['pilot']['replay']['metadata_recovered_from_verified_payloads']['counts']['payload_observations_replayed']==2
    assert PREFIX.encode() not in r.encode(report) and TOKEN.encode() not in r.encode(report)
    assert all(record['unchanged'] for record in report['database_hashes'].values())


@pytest.mark.parametrize('table,code',[('sec_liquidity_runs','RUN_LINEAGE_MISMATCH'),
    ('sec_liquidity_checkpoints','CHECKPOINT_LINEAGE_MISMATCH')])
def test_difference_at_final_character_refuses_selected_lineage(tmp_path,table,code):
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute(f'UPDATE {table} SET plan_id=?',[different_end()])
    report=run(paths)
    assert report['execution_state']=='failed' and report['errors']==[code]
    assert 'replay' not in report['pilot']
    assert all(record['unchanged'] for record in report['database_hashes'].values())
    assert PREFIX.encode() not in r.encode(report)


@pytest.mark.parametrize('table',['sec_facts','sec_liquidity_raw_provenance'])
def test_fact_or_provenance_tail_difference_never_joins_or_recovers(tmp_path,table):
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        if table=='sec_facts':db.execute('UPDATE sec_facts SET ingestion_plan_id=?',[different_end()])
        else:db.execute("UPDATE sec_liquidity_raw_provenance SET plan_id=? WHERE endpoint_class='submissions'",[different_end()])
    report=run(paths)
    assert report['pilot']['state']=='no_qualifying_pair'
    assert report['pilot']['qualifying_pair_count']==0 and 'replay' not in report['pilot']
    assert PREFIX.encode() not in r.encode(report)


@pytest.mark.parametrize('value',[None,''])
def test_null_empty_preserved_and_never_valid_identities(tmp_path,value):
    # Nullable synthetic projection tables also cover legacy/unknown schemas.
    with duckdb.connect(':memory:',config=r.history._sql_config()) as db:
        for table in TABLES:
            column='ingestion_plan_id' if table=='sec_facts' else 'plan_id'
            db.execute(f'CREATE TABLE {table}({column} VARCHAR)')
            db.execute(f'INSERT INTO {table} VALUES (?)',[value])
            _,rows=r.metadata_rows(db,table,(column,),{column},r.MAX_ROWS)
            assert all(row[r.OPAQUE_REFERENCES[column]]==value for row in rows)
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        if value is None:
            db.execute('UPDATE sec_facts SET ingestion_plan_id=NULL')
        else:
            for table in TABLES:
                column='ingestion_plan_id' if table=='sec_facts' else 'plan_id'
                db.execute(f'UPDATE {table} SET {column}=?',[value])
    report=run(paths)
    assert report['pilot']['state']=='no_qualifying_pair' and 'replay' not in report['pilot']


@pytest.mark.parametrize('sql,code',[
    ("DELETE FROM sec_liquidity_checkpoints",'CHECKPOINT_LINEAGE_UNPROVEN'),
    ("UPDATE sec_liquidity_checkpoints SET run_id='other'",'CHECKPOINT_LINEAGE_MISMATCH'),
    ("UPDATE sec_liquidity_checkpoints SET operation_contract_version='other'",'CHECKPOINT_LINEAGE_MISMATCH'),
    ("UPDATE sec_liquidity_checkpoints SET transaction_succeeded=false",'CHECKPOINT_COMPLETION_UNPROVEN'),
    ("UPDATE sec_liquidity_checkpoints SET status='failed'",'CHECKPOINT_COMPLETION_UNPROVEN'),
    ("UPDATE sec_liquidity_checkpoints SET updated_at=TIMESTAMPTZ '2027-01-01 00:00:00+00'",'CHECKPOINT_METADATA_NOT_VISIBLE'),
])
def test_checkpoint_proof_not_dropped_or_assumed(tmp_path,sql,code):
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute(sql)
    report=run(paths)
    assert report['execution_state']=='failed' and report['errors']==[code]
    assert PREFIX.encode() not in r.encode(report)


def test_checkpoint_row_in_same_total_budget(tmp_path):
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0]),read_only=True) as db:
        _,pair=r.metadata_rows(db,r.RAW_TABLE,r.MANIFEST,set(r.MANIFEST)|{'payload_json'},r.MAX_MANIFEST)
        pair.sort(key=lambda row:row['endpoint_class'])
        with pytest.raises(r.ReplayError,match='METADATA_ROW_LIMIT'):r.verify_lineage(db,pair,remaining=1,decision_at=D)


def test_full_length_reports_from_exact_verifier_do_not_leak_tokens(tmp_path):
    paths=full_length_databases(tmp_path);ns,_=runner();ns['execute']=simulate(paths)
    summary=verify(tmp_path,ns,paths)
    assert summary['execution_state']=='completed',summary
    for filename in ('track-b-provenance-replay.json','track-b-provenance-verification.json'):
        content=(tmp_path/'reports'/filename).read_bytes()
        assert PREFIX.encode() not in content and TOKEN.encode() not in content
        assert not content.startswith(b'\xef\xbb\xbf')


def test_unchanged_limits_and_payload_path(tmp_path):
    paths=full_length_databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute('UPDATE sec_facts SET frame=?',['x'*1025])
    report=run(paths)
    assert report['errors']==['METADATA_CELL_LIMIT']
    assert report['safe_read_diagnostic']['offending_columns'][0]['source_column']=='frame'
    assert r.MAX_CELL==1024 and r.MAX_PAYLOAD==5000000 and r.MAX_TOTAL_PAYLOAD==10000000
    assert r.MAX_ACCESSIONS==3 and r.MAX_OBSERVATIONS==24 and r.MAX_REPORT==131072
