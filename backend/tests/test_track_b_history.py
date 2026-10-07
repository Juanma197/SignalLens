"""Offline fixtures only; numerical columns deliberately contain poison values."""
from datetime import date, datetime, timedelta, timezone
import json
import subprocess
import sys

import duckdb
import pytest

import app.track_b_history as history
from app.track_b_history import inventory, _accounting_state, _chains, SOURCES
from app.track_b_panel import PROHIBITED_ARRAYS
from app.investment_research import InvestmentResearchError
from app.global_universe import utc_naive

DECISION = datetime(2026, 10, 5, 0, 30, tzinfo=timezone.utc)


def fixture(tmp_path):
    research, production = tmp_path/'research.duckdb', tmp_path/'production.duckdb'
    for path in (research, production):
        with duckdb.connect(str(path)) as db: db.execute('CREATE TABLE marker(secret VARCHAR)')
    with duckdb.connect(str(research)) as db:
        db.execute('''CREATE TABLE canonical_factor_evidence(
            security_id VARCHAR, canonical_field VARCHAR, original_concept_or_field VARCHAR,
            unit VARCHAR, currency VARCHAR, period_start DATE, period_end DATE,
            public_at TIMESTAMPTZ, retrieved_at TIMESTAMPTZ, available_at TIMESTAMPTZ,
            materialized_at TIMESTAMPTZ, reliability_state VARCHAR, alias_contract_version VARCHAR,
            accession_or_source_identifier VARCHAR, materialization_run_id VARCHAR, value VARCHAR)''')
        db.execute('''CREATE TABLE security_listings(security_id VARCHAR, active BOOLEAN,
            retrieval_id VARCHAR, cik VARCHAR)''')
        db.execute("INSERT INTO security_listings VALUES ('one', false, 'r1', '0000000001')")
        db.execute('''CREATE TABLE issuer_mapping_candidates(security_id VARCHAR, cik VARCHAR,
            effective_from TIMESTAMPTZ, effective_to TIMESTAMPTZ, observed_at TIMESTAMPTZ,
            review_status VARCHAR, conflict_state VARCHAR, ticker_reuse_protected BOOLEAN)''')
        db.execute("INSERT INTO issuer_mapping_candidates VALUES ('one','0000000001',?,NULL,?,'approved','none',true)",
                   [DECISION-timedelta(days=1000), DECISION])
        # Actual amounts are deliberately not valid numbers. Reading them would
        # violate the metadata-only contract; no value validation is claimed.
        for field in ('operating_cash_flow', 'capital_expenditure', 'revenue', 'net_income'):
            for start, end in quarters(): insert(db, field, start, end)
        for day in (date(2023,12,31),date(2024,12,31),date(2025,12,31)):
            for field in ('assets', 'current_debt', 'non_current_debt', 'cash_and_cash_equivalents'):
                insert(db, field, None, day)
        for year in (2024,2025): insert(db, 'diluted_shares', date(year,1,1),date(year,12,31))
        db.execute('''CREATE TABLE global_price_observations(qualified_symbol VARCHAR,
            trading_date DATE, retrieved_at TIMESTAMPTZ, close VARCHAR, adjusted_close VARCHAR)''')
        db.execute("INSERT INTO global_price_observations VALUES ('ONE.US','2026-10-02',?,'POISON','POISON')", [DECISION])
        db.execute('''CREATE TABLE global_corporate_actions(qualified_symbol VARCHAR, ex_date DATE,
            action_type VARCHAR, retrieved_at TIMESTAMPTZ, value VARCHAR)''')
        db.execute("INSERT INTO global_corporate_actions VALUES ('ONE.US','2026-09-01','cash_distribution',?,'POISON')", [DECISION])
        db.execute('CREATE TABLE research_shadow_outcomes(realized_return VARCHAR)')
        db.execute("INSERT INTO research_shadow_outcomes VALUES ('DO NOT READ')")
    return research, production


def quarters():
    return [(date(year, month, 1), date(year+(month==10), (month+3-1)%12+1, 1)-timedelta(days=1))
            for year in (2024,2025) for month in (1,4,7,10)]


def insert(db, field, start, end, sid='one', stamp=DECISION, **overrides):
    _, unit, concepts = history.FIELDS[field]
    row = {'security_id':sid, 'canonical_field':field, 'original_concept_or_field':concepts[0],
        'unit':unit, 'currency':'USD' if unit=='USD' else None, 'period_start':start, 'period_end':end,
        'public_at':stamp, 'retrieved_at':stamp, 'available_at':stamp,
        'materialized_at':stamp, 'reliability_state':'usable', 'alias_contract_version':'fixture',
        'accession_or_source_identifier':'fixture-ref', 'materialization_run_id':None, 'value':'POISON'}
    row.update(overrides)
    columns=','.join(row); placeholders=','.join('?' for _ in row)
    db.execute(f'INSERT INTO canonical_factor_evidence({columns}) VALUES ({placeholders})', list(row.values()))


def run(paths, **kwargs):
    return inventory(research_db=paths[0], production_db=paths[1], decision_at=DECISION, **kwargs)


def test_deterministic_bounded_immutable_metadata_only(tmp_path):
    paths = fixture(tmp_path); before = tuple(p.read_bytes() for p in paths)
    r = run(paths)
    assert run(paths) == r
    assert tuple(p.read_bytes() for p in paths) == before
    assert r['metadata_only'] and not r['realized_outcome_values_read']
    assert len(r['sources']) == len(SOURCES)*2
    assert r['compact_utf8_bytes'] == len(json.dumps(r,sort_keys=True,separators=(',',':')).encode())
    assert r['compact_utf8_bytes'] <= history.MAXIMUM_BYTES
    assert all(r[k] == [] for k in PROHIBITED_ARRAYS)
    assert r['validation_credit'] == 0 and r['contract_selected'] is None
    assert r['sample_thresholds'] is None and not r['preregistration_ready']
    assert len(r['blockers']) == 8 and all(x['state']=='unresolved' for x in r['blockers'])
    assert all(v['unchanged'] for v in r['database_fingerprints'].values())
    canonical = r['databases']['research']['canonical_history']
    for family in canonical['families'].values():
        assert family['securities_with_structural_chain'] == 1
        assert sum(family['primary_gap_counts'].values()) == family['population_denominator']
        assert family['certified_formula_count'] == 0
    market = r['databases']['research']['market_metadata']
    assert market['dividends']['metadata_row_count'] == 1
    assert market['delistings']['state'] == 'absent_evidence'
    assert market['benchmark']['approved_benchmark'] is None
    identity = r['databases']['research']['identity']
    assert identity['inactive_listing_row_count'] == 1 and identity['delisting_action_metadata_row_count'] == 0
    assert not identity['effective_identity_certified']
    text = json.dumps(r)
    assert 'POISON' not in text and 'DO NOT READ' not in text and str(tmp_path) not in text


def test_explicit_projection_and_no_outcome_tables(tmp_path, monkeypatch):
    paths = fixture(tmp_path); queries=[]
    original = history.duckdb.connect
    class Spy:
        def __init__(self, db): self.db=db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self,*args): return self.db.__exit__(*args)
        def execute(self,sql,*args):
            queries.append(sql)
            # count(*) is a row metadata aggregate, not an observation read.
            assert 'SELECT * ' not in sql
            assert '"value"' not in sql and '"close"' not in sql and '"adjusted_close"' not in sql
            assert 'research_shadow_outcomes' not in sql
            return self.db.execute(sql,*args)
    def connect(path, **kwargs):
        assert kwargs == {'read_only':True, 'config':history._sql_config()}
        return Spy(original(path, **kwargs))
    monkeypatch.setattr(history.duckdb, 'connect', connect)
    run(paths)
    assert queries


def test_boundary_and_provenance_states(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        insert(db,'revenue',date(2026,1,1),date(2026,3,31),sid='future',stamp=DECISION+timedelta(microseconds=1))
        insert(db,'revenue',date(2026,1,1),date(2026,3,31),sid='badunit',unit='EUR')
        insert(db,'revenue',date(2026,1,1),date(2026,3,31),sid='unknown',available_at=None)
        insert(db,'revenue',date(2026,1,1),date(2026,3,31),sid='materialized',
               materialization_run_id='r',materialized_at=DECISION+timedelta(microseconds=1),
               available_at=DECISION+timedelta(microseconds=1))
    rows = run(paths)['databases']['research']['canonical_history']
    states = rows['fields']['revenue']['observation_states']
    assert states == {'incompatible_evidence':1,'unverified_provenance':1,'post_boundary':2,'metadata_compatible_unverified':8}
    assert rows['families']['growth']['population_denominator'] == 5
    assert rows['families']['growth']['primary_gap_counts']['structural_chain_present_unverified'] == 1
    assert rows['fields']['operating_cash_flow']['absent_security_count'] == 4
    # Public/retrieval equality is eligible; malformed and naive stamps never are.
    row = {'original_concept_or_field':'Assets','unit':'USD','currency':'USD','period_end':date(2025,12,31),
        'public_at':DECISION,'retrieved_at':DECISION,'available_at':DECISION,'reliability_state':'usable',
        'alias_contract_version':'x','accession_or_source_identifier':'y','security_id':'one'}
    assert _accounting_state(row,'assets','canonical',DECISION)[0] == 'metadata_compatible_unverified'
    assert _accounting_state(row|{'public_at':DECISION.replace(tzinfo=None)},'assets','canonical',DECISION)[0] == 'unverified_provenance'
    for key in ('original_concept_or_field','unit','currency','period_end','reliability_state'):
        assert _accounting_state(row|{key:None},'assets','canonical',DECISION)[0] == 'unverified_provenance'
    assert _accounting_state(row|{'currency':'EUR'},'assets','canonical',DECISION)[0] == 'incompatible_evidence'


def test_period_chain_rejects_ytd_gaps_overlap_and_currency_mixing():
    q = quarters()
    assert _chains(q,8) == [(date(2024,1,1),date(2025,12,31))]
    assert _chains(q[:3]+q[4:],8) == []
    assert _chains([(date(2025,1,1),date(2025,6,30)),(date(2025,1,1),date(2025,9,30))],2) == []
    assert _chains(q+q,8) == _chains(q,8)  # metadata duplicate, never revision/value selection
    assert _chains([(date(2024,1,1),date(2024,12,31)),(date(2025,1,1),date(2025,12,31))],2,True)


def test_chain_work_bound_fails_closed(monkeypatch):
    monkeypatch.setattr(history,'MAX_CHAIN_WORK',1)
    with pytest.raises(history.InventoryError): _chains(quarters(),8)


@pytest.mark.parametrize('duckdb_session_timezone', ['UTC', 'Europe/London'], indirect=True)
def test_market_utc_storage_and_absent_incompatible_schema_distinction(tmp_path, duckdb_session_timezone):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        assert db.execute("SELECT current_setting('TimeZone')").fetchone()[0] == duckdb_session_timezone
        # A plain cast retains session-local wall time, not the producer's UTC
        # wall time. Demonstrate the defect before constructing UTC-naive storage.
        plain, utc = db.execute("SELECT CAST(? AS TIMESTAMP), timezone('UTC', ?)",
                                [DECISION, DECISION]).fetchone()
        assert utc == utc_naive(DECISION)
        assert plain == utc + timedelta(hours=duckdb_session_timezone == 'Europe/London')
        for table in ('global_price_observations', 'global_corporate_actions'):
            db.execute(f"ALTER TABLE {table} ALTER COLUMN retrieved_at TYPE TIMESTAMP USING timezone('UTC', retrieved_at)")
            assert db.execute(f'SELECT retrieved_at FROM {table}').fetchone()[0] == utc
        # One microsecond after the boundary must remain excluded.
        db.execute("INSERT INTO global_price_observations VALUES ('FUTURE.US','2026-10-02',?,'POISON','POISON')",
                   [utc + timedelta(microseconds=1)])
        db.execute("INSERT INTO global_corporate_actions VALUES ('FUTURE.US','2026-09-01','cash_distribution',?,'POISON')",
                   [utc + timedelta(microseconds=1)])
    with duckdb.connect(str(paths[1])) as db:
        db.execute('CREATE TABLE global_corporate_actions(unknown_column VARCHAR)')
        db.execute("INSERT INTO global_corporate_actions VALUES ('unknown')")
        db.execute('CREATE TABLE canonical_factor_evidence(unknown_column VARCHAR)')
        db.execute("INSERT INTO canonical_factor_evidence VALUES ('unknown')")
    r=run(paths)
    assert r['databases']['research']['market_metadata']['dividends']['retrieved_by_boundary_count']==1
    assert r['databases']['research']['market_metadata']['price_and_risk']['metadata_observation_states']['metadata_compatible_unverified']==1
    assert r['databases']['research']['market_metadata']['dividends']['metadata_row_count']==2
    assert r['databases']['research']['market_metadata']['price_and_risk']['metadata_observation_states']['post_boundary']==1
    other=r['databases']['production']
    assert other['market_metadata']['dividends']['state']=='incompatible_evidence'
    assert other['market_metadata']['dividends']['metadata_row_count'] is None
    assert other['canonical_history']['fields']['revenue']['absent_security_count'] is None


def test_raw_sec_not_promoted_and_production_kept_separate(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:
        db.execute('''CREATE TABLE sec_facts(security_id VARCHAR,cik VARCHAR,concept VARCHAR,taxonomy VARCHAR,
            unit VARCHAR,currency VARCHAR,period_start DATE,period_end DATE,public_at TIMESTAMPTZ,
            retrieved_at TIMESTAMPTZ,accession_number VARCHAR,source_endpoint VARCHAR,value VARCHAR)''')
        for s,e in quarters(): db.execute("INSERT INTO sec_facts VALUES ('raw','0000000001','Revenues','us-gaap','USD','USD',?,?,?,?,'x','fixture','POISON')",[s,e,DECISION,DECISION])
    r = run(paths)
    production=r['databases']['production']
    assert production['raw_sec_history']['families']['growth']['securities_with_structural_chain']==1
    assert production['canonical_history']['families']['growth']['securities_with_structural_chain']==0
    assert r['databases']['research']['canonical_history']['families']['growth']['population_denominator']==1


def test_empty_unsupported_view_work_and_size_bounds(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:
        db.execute("CREATE VIEW global_corporate_actions AS SELECT error('DO NOT EVALUATE') AS value")
    r = run(paths)
    source=next(s for s in r['sources'] if s['database']=='production' and s['table']=='global_corporate_actions')
    assert source['state']=='incompatible_evidence' and source['reason']=='views_not_evaluated'
    assert r['databases']['production']['market_metadata']['dividends']['state']=='incompatible_evidence'
    before=tuple(p.read_bytes() for p in paths)
    monkeypatch.setattr(history,'MAX_METADATA_ROWS',1)
    with pytest.raises(history.InventoryError): run(paths)
    assert tuple(p.read_bytes() for p in paths)==before
    monkeypatch.setattr(history,'MAX_METADATA_ROWS',500_000)
    monkeypatch.setattr(history,'MAXIMUM_BYTES',1)
    with pytest.raises(InvestmentResearchError): run(paths)


def test_sample_bounds_preserve_exact_population(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        for i in range(12):
            for s,e in quarters(): insert(db,'revenue',s,e,sid=f'extra{i:02}')
    r=run(paths)['databases']['research']['canonical_history']['families']['growth']
    assert r['securities_with_structural_chain']==r['population_denominator']==13
    assert r['samples']['returned_count']==10 and r['samples']['total_count']==13 and r['samples']['truncated']


def test_hash_checks_even_on_failure(tmp_path, monkeypatch):
    paths=fixture(tmp_path); calls=[]
    original=history.fingerprint
    def fingerprint(path): calls.append(path); return original(path)
    monkeypatch.setattr(history,'fingerprint',fingerprint)
    monkeypatch.setattr(history,'_read',lambda *a: (_ for _ in ()).throw(RuntimeError('secret')))
    with pytest.raises(RuntimeError): run(paths)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]


def test_changed_fingerprint_fails_closed(tmp_path, monkeypatch):
    paths=fixture(tmp_path); original=history.fingerprint; calls=0
    def fingerprint(path):
        nonlocal calls
        calls+=1
        result=original(path)
        if calls==3: result['sha256']='changed'
        return result
    monkeypatch.setattr(history,'fingerprint',fingerprint)
    with pytest.raises(history.InventoryError): run(paths)


def test_cli_success_and_stable_redacted_failure(tmp_path):
    paths=fixture(tmp_path)
    command=[sys.executable,'-m','app.investment_research_cli','track-b-historical-evidence-inventory',
        '--research-db',str(paths[0]),'--production-db',str(paths[1]),'--decision-at',DECISION.isoformat()]
    success=subprocess.run(command,capture_output=True,text=True)
    assert success.returncode==0 and not success.stderr and json.loads(success.stdout)==run(paths)
    command[-1]=DECISION.replace(tzinfo=None).isoformat()
    failed=subprocess.run(command,capture_output=True,text=True)
    assert failed.returncode==1 and not failed.stdout
    payload=json.loads(failed.stderr)
    assert payload['error']['code']=='INVESTMENT_RESEARCH_NOT_READY'
    assert str(tmp_path) not in failed.stderr and 'Traceback' not in failed.stderr
    with pytest.raises(ValueError): inventory(research_db=paths[0],production_db=paths[0],decision_at=DECISION)
