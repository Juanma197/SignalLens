"""Operator-shaped population; synthetic databases and poison values only."""
from datetime import datetime, timedelta, timezone
import json

import duckdb
import pytest

import app.track_b_history as h
from test_track_b_history import fixture
from app.investment_evidence import SCHEMA
from app.track_b_history_diagnostic import diagnose

HISTORICAL = datetime(2026, 10, 4, 21, 30, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)


def test_complete_million_row_population(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('DROP TABLE global_price_observations')
        db.execute('''CREATE TABLE global_price_observations AS
            SELECT 'S' || (i//253)::VARCHAR AS qualified_symbol,
              (DATE '2025-01-01' + (i%253)::INTEGER)::VARCHAR AS trading_date,
              'available' AS status,
              TIMESTAMP '2026-10-04 21:30:00' +
                CASE WHEN i//253 >= 2048 AND i%253=252 THEN INTERVAL 1 MICROSECOND ELSE INTERVAL 0 MICROSECOND END AS retrieved_at,
              'POISON' AS "close", 'POISON' AS volume, 'POISON' AS realized_return
            FROM range(1036288) r(i)''')
        db.execute('''INSERT INTO global_price_observations
            SELECT 'S0', '2025-01-01', 'available', TIMESTAMP '2026-10-04 21:30:00', 'POISON','POISON','POISON'
            FROM range(10000)''')
        db.execute('''INSERT INTO global_price_observations VALUES
            ('bad-date','invalid','available',TIMESTAMP '2026-10-04 21:30:00','POISON','POISON','POISON'),
            ('no-date',NULL,'available',TIMESTAMP '2026-10-04 21:30:00','POISON','POISON','POISON'),
            ('bad-status','2025-01-01','failed',TIMESTAMP '2026-10-04 21:30:00','POISON','POISON','POISON'),
            ('no-stamp','2025-01-01','available',NULL,'POISON','POISON','POISON'),
            (NULL,'2025-01-01','available',TIMESTAMP '2026-10-04 21:30:00','POISON','POISON','POISON'),
            ('future-date','2026-10-06','available',TIMESTAMP '2026-10-04 21:30:00','POISON','POISON','POISON')''')
    before = tuple(h.fingerprint(p) for p in paths)
    original = h.duckdb.connect
    queries = []; batch_sizes = []
    class Spy:
        def __init__(self, db): self.db=db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self,*args): return self.db.__exit__(*args)
        def execute(self,sql,*args):
            queries.append(sql)
            assert 'SELECT * ' not in sql
            assert all('"'+c+'"' not in sql for c in ('close','volume','realized_return'))
            self.cursor=self.db.execute(sql,*args); return self
        def fetchone(self): return self.cursor.fetchone()
        def fetchall(self): return self.cursor.fetchall()
        def fetchmany(self,n):
            rows=self.cursor.fetchmany(n); batch_sizes.append(len(rows)); return rows
    def connect(path, **kwargs):
        assert kwargs == {'read_only': True, 'config':h._sql_config()}
        return Spy(original(path, **kwargs))
    monkeypatch.setattr(h.duckdb, 'connect', connect)
    reports = []
    for decision, qualified, distinct, visible, future in (
        (HISTORICAL,2048,1034240,1044240,2049),
        (LATER,4096,1036288,1046288,1),
    ):
        r=h.inventory(research_db=paths[0], production_db=paths[1], decision_at=decision)
        reports.append(r)
        source=next(s for s in r['sources'] if s['database']=='research' and s['table']=='global_price_observations')
        assert source['row_count']==1046294 > 1035884
        assert source['date_ranges']['trading_date']=={
            'first':'2025-01-01 00:00:00','last':'2026-10-06 00:00:00','invalid_count':1,'missing_count':1}
        assert source['date_ranges']['retrieved_at']=={
            'first':'2026-10-04 21:30:00','last':'2026-10-04 21:30:00.000001','invalid_count':0,'missing_count':1}
        market=r['databases']['research']['market_metadata']['price_and_risk']
        assert market['symbols_with_253_date_metadata_rows']==qualified
        assert market['boundary_visible_distinct_symbol_date_count']==distinct
        assert market['metadata_observation_states']=={
            'incompatible_evidence':3,'unverified_provenance':2,'post_boundary':future,'metadata_compatible_unverified':visible}
        assert sum(market['metadata_observation_states'].values())==source['row_count']
        assert r['databases']['production']['market_metadata']['price_and_risk']['boundary_visible_distinct_symbol_date_count']==0
        text=json.dumps(r,sort_keys=True,separators=(',',':'))
        assert len(text.encode())==r['compact_utf8_bytes']<=h.MAXIMUM_BYTES
        assert 'POISON' not in text and str(tmp_path) not in text
        assert all(f['samples']['returned_count']<=10 for layer in ('canonical_history','raw_sec_history')
                   for f in r['databases']['research'][layer]['families'].values())
    assert h.inventory(research_db=paths[0], production_db=paths[1], decision_at=HISTORICAL)==reports[0]
    assert tuple(h.fingerprint(p) for p in paths)==before
    assert max(batch_sizes)<=h.MARKET_BATCH_ROWS
    assert any('ORDER BY encode(CAST("qualified_symbol" AS VARCHAR))' in q for q in queries)


@pytest.mark.parametrize('bound', ['dates','cell','sql'])
def test_market_resources_fail_closed_and_immutable(tmp_path, monkeypatch, bound):
    paths=fixture(tmp_path)
    before=tuple(h.fingerprint(p) for p in paths)
    if bound=='dates': monkeypatch.setattr(h,'MAX_MARKET_DATES_PER_SYMBOL',0)
    elif bound=='cell': monkeypatch.setattr(h,'MAX_MARKET_METADATA_CHARS',1)
    else: monkeypatch.setattr(h,'MARKET_SQL_MEMORY','1B')
    with pytest.raises(h.InventoryError):
        h.inventory(research_db=paths[0],production_db=paths[1],decision_at=LATER)
    assert tuple(h.fingerprint(p) for p in paths)==before


def test_established_legacy_schema_one_optional_column(tmp_path):
    paths=fixture(tmp_path)
    ddl=SCHEMA.split('CREATE TABLE IF NOT EXISTS canonical_factor_evidence(',1)[1].split(';',1)[0]
    with duckdb.connect(str(paths[0])) as db:
        db.execute('DROP TABLE canonical_factor_evidence')
        db.execute('CREATE TABLE canonical_factor_evidence('+ddl)
        db.execute('''INSERT INTO canonical_factor_evidence
            (evidence_key,security_id,canonical_field,unit,currency,period_start,period_end,
             accession_or_source_identifier,public_at,retrieved_at,available_at,materialized_at,
             original_concept_or_field,alias_contract_version,sign_convention,reliability_state,provenance,lineage)
             VALUES ('legacy','one','revenue','USD','USD','2026-01-01','2026-03-31',
                     'fixture',?,?,?,?,'Revenues','fixture','credit','usable','{}','{}')''',
                   [HISTORICAL,HISTORICAL,HISTORICAL,LATER+timedelta(days=1)])
    before=tuple(h.fingerprint(p) for p in paths)
    r=h.inventory(research_db=paths[0],production_db=paths[1],decision_at=HISTORICAL)
    source=next(s for s in r['sources'] if s['database']=='research' and s['table']=='canonical_factor_evidence')
    assert source['missing_adapter_columns']==['materialization_run_id']
    canonical=r['databases']['research']['canonical_history']
    assert canonical['fields']['revenue']['observation_states']['metadata_compatible_unverified']==1
    assert canonical['fields']['revenue']['observation_states']['post_boundary']==0
    assert r['databases']['research']['raw_sec_history']['fields']['revenue']['observation_count']==0
    events=diagnose(research_db=paths[0],production_db=paths[1],decision_at=HISTORICAL)['events']
    assert any(e['stage']=='research.canonical_factor_evidence' and
               e['reason_code']=='SCHEMA_PARTIAL' and e['counts']['missing_column_count']==1 for e in events)
    assert any(e['reason_code']=='INVENTORY_COMPLETED' for e in events)
    assert tuple(h.fingerprint(p) for p in paths)==before


def test_binary_symbol_grouping_and_timestamp_conventions(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('DROP TABLE global_price_observations')
        db.execute('''CREATE TABLE global_price_observations(
            qualified_symbol VARCHAR COLLATE NOCASE, trading_date DATE, retrieved_at VARCHAR)''')
        db.execute('''INSERT INTO global_price_observations
            SELECT CASE WHEN i%2=0 THEN 'a' ELSE 'A' END,
                   DATE '2025-01-01' + (i//2)::INTEGER, '2026-10-04T21:30:00+00:00'
            FROM range(506) r(i)''')
        # UTC-naive text is not the producer's datetime TIMESTAMP convention.
        db.execute("INSERT INTO global_price_observations VALUES ('naive','2025-01-01','2026-10-04T21:30:00')")
    with duckdb.connect(str(paths[0]),read_only=True,config=h._sql_config()) as db:
        settings=('memory_limit','threads','temp_directory','max_temp_directory_size')
        before=[db.execute('SELECT current_setting(?)',[s]).fetchone()[0] for s in settings]
        _,prices=h._read(db,'global_price_observations',HISTORICAL)
        assert [db.execute('SELECT current_setting(?)',[s]).fetchone()[0] for s in settings]==before
    assert prices['symbols_with_253_date_metadata_rows']==2
    assert prices['boundary_visible_distinct_symbol_date_count']==506
    assert prices['metadata_observation_states']['unverified_provenance']==1


def test_missing_market_date_column_exact_aggregate(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('DROP TABLE global_price_observations')
        db.execute("CREATE TABLE global_price_observations AS SELECT 'POISON' AS source FROM range(1036000)")
    r=h.inventory(research_db=paths[0],production_db=paths[1],decision_at=HISTORICAL)
    market=r['databases']['research']['market_metadata']['price_and_risk']
    assert market['metadata_observation_states']['incompatible_evidence']==1036000
    assert market['boundary_visible_distinct_symbol_date_count']==0
