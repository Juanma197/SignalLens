"""Offline end-to-end slice: fresh temporary databases, real CLI and guarded API."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import socket
import subprocess
import sys

from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
import pytest

from app.prototype import service
from app.prototype.fixture import create_fixture, DECISION
from app.model_readiness import fingerprint


@pytest.fixture
def paths(prototype_fixture):
    return tuple(Path(p) for p in prototype_fixture)


def run(paths, **kwargs):
    return service.assess(research_db=paths[0], production_db=paths[1], decision_at=DECISION, **kwargs)


def change(paths, sql, args=None):
    with duckdb.connect(str(paths[0])) as db: db.execute(sql, args or [])


def company(report, number=0):
    return next(c for c in report['companies'] if c['security_id'] == f'synthetic-{number:02}')


def test_roster_screen_citations_and_immutable_files(paths, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *args: pytest.fail('Network access forbidden'))
    before = [fingerprint(p) for p in paths]
    r = run(paths)
    assert r['eligible_count'] == 18 and len(r['proposed_membership']) == 15
    assert r['proposed_membership'] == sorted(r['eligible_roster'], key=lambda sid:(hashlib.sha256((service.CONFIG['version']+':'+sid).encode()).hexdigest(),sid))[:15]
    # Synthetic returns rise with the company number; only members can qualify.
    members = r['proposed_membership']
    assert r['results'] == sorted([m for m in members if int(m[-2:]) > 3], key=lambda m: -int(m[-2:]))[:3]
    assert r['validation_credit'] == r['writes'] == r['provider_requests'] == 0
    assert r['membership_state'] == 'proposed_unfrozen' and r['operator_review_required']
    assert r['configuration']['weights'] is None and r['databases_unchanged']
    assert r['synthetic_fixture'] and not r['blockers']
    n = int(r['results'][0][-2:]); c = company(r,n); calc = c['calculation']
    assert calc['session_intervals']==126
    assert calc['momentum_return']==pytest.approx(calc['end_adjusted_close']/calc['start_adjusted_close']-1)
    assert c['direct_evidence'][0]['citation']['fact_key']==f'fact-{n}'
    assert len(c['missing_data'])==5
    assert c['action_coverage']['coverage_state']=='verified_no_action'
    assert c['identity_evidence']['source_identifier']==f'identity-{n}' and c['cik']==f'{n+100:010}'
    assert r['session_calendar']['derived_sessions']==127 and r['session_calendar']['us_symbols_priced']==18
    assert [fingerprint(p) for p in paths] == before


def test_membership_does_not_depend_on_momentum_or_financial_amounts(paths):
    before = run(paths)
    change(paths, 'UPDATE global_price_observations SET adjusted_close=100,close=100,open=100,high=101,low=99')
    change(paths, "UPDATE sec_facts SET value=value*100 WHERE taxonomy='us-gaap'")
    after = run(paths)
    assert before['proposed_membership'] == after['proposed_membership']
    assert before['eligible_roster'] == after['eligible_roster']
    assert after['results'] == []


def test_future_returns_and_late_evidence_do_not_choose_members(paths):
    before = run(paths)
    change(paths,"INSERT INTO global_price_observations SELECT qualified_symbol,DATE '2026-10-02',exchange,currency,open,high,low,close,99999999,volume,status,source,TIMESTAMP '2026-10-02 23:00:00' FROM global_price_observations WHERE trading_date=(SELECT max(trading_date) FROM global_price_observations)")
    change(paths,"INSERT INTO sec_facts SELECT fact_key||'-late',security_id,qualified_symbol,ticker,'9999999999',taxonomy,concept,value,unit,currency,period_start,period_end,fiscal_year,fiscal_period,frame,form,accession_number,filed_date,public_at,is_amendment,is_revision,source_endpoint,TIMESTAMPTZ '2026-10-02 00:00:00Z' FROM sec_facts")
    after = run(paths)
    assert before['proposed_membership'] == after['proposed_membership']
    assert before['results'] == after['results'] and before['companies'] == after['companies']


@pytest.mark.parametrize(('sql','reason'), [
    ("UPDATE issuer_mapping_candidates SET conflict_state='ticker_reused' WHERE security_id='synthetic-00'", 'issuer_mapping_conflict'),
    ("UPDATE sec_issuers SET cik='0000009999' WHERE security_id='synthetic-00'", 'classification_cik_mismatch'),
    ("DELETE FROM sec_issuers WHERE security_id='synthetic-00'", 'stored_cik_mapping_missing_or_conflicting'),
    ("INSERT INTO sec_issuers SELECT security_id,qualified_symbol,ticker,'0000009999',issuer_name,mapping_source,mapped_at FROM sec_issuers WHERE security_id='synthetic-00'", 'stored_cik_mapping_missing_or_conflicting'),
    ("UPDATE security_listings SET cik='0000009999' WHERE security_id='synthetic-00'", 'listing_cik_mismatch'),
    ("UPDATE security_classification_evidence SET review_required=true WHERE security_id='synthetic-00'", 'classification_review_required'),
    ("UPDATE security_classification_evidence SET conflict_details='{\"conflict\":true}' WHERE security_id='synthetic-00'", 'classification_ambiguous'),
    ("DELETE FROM global_price_observations WHERE qualified_symbol='SYN00.US' AND trading_date=(SELECT min(trading_date) FROM global_price_observations)", 'missing_exact_session_prices'),
    ("UPDATE global_price_observations SET high=0 WHERE qualified_symbol='SYN00.US'", 'invalid_price_history'),
    ("DELETE FROM corporate_action_coverage_evidence WHERE security_id='synthetic-00'", 'corporate_action_coverage_missing_or_incomplete'),
    ("UPDATE corporate_action_coverage_evidence SET assessed_from=DATE '2026-09-01' WHERE security_id='synthetic-00'", 'corporate_action_coverage_missing_or_incomplete'),
    ("UPDATE corporate_action_coverage_evidence SET coverage_state='unresolved_action' WHERE security_id='synthetic-00'", 'unresolved_or_conflicting_corporate_action_coverage'),
    ("UPDATE sec_facts SET retrieved_at=TIMESTAMPTZ '2026-10-02 00:00:00Z' WHERE security_id='synthetic-00'", 'no_usable_direct_financial_evidence'),
    ("UPDATE sec_facts SET unit='EUR' WHERE security_id='synthetic-00' AND taxonomy='us-gaap'", 'no_usable_direct_financial_evidence'),
    ("UPDATE sec_facts SET value=-1 WHERE security_id='synthetic-00' AND taxonomy='us-gaap'", 'no_usable_direct_financial_evidence'),
    ("UPDATE sec_facts SET source_endpoint='https://unsafe.example' WHERE security_id='synthetic-00' AND taxonomy='us-gaap'", 'no_usable_direct_financial_evidence'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"3560\"','\"6798\"') WHERE security_id='synthetic-00'", 'specialist_sector_excluded'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"3560\"','\"6021\"') WHERE security_id='synthetic-00'", 'specialist_sector_excluded'),
    ("UPDATE security_listings SET company_name='Synthetic Royalty Partners LP' WHERE security_id='synthetic-00'", 'partnership_units_excluded'),
    ("DELETE FROM sec_liquidity_raw_provenance WHERE security_id='synthetic-00'", 'industry_classification_unavailable'),
    ("UPDATE sec_liquidity_raw_provenance SET retrieved_at=TIMESTAMPTZ '2026-10-02 00:00:00Z' WHERE security_id='synthetic-00'", 'industry_classification_unavailable'),
    ("UPDATE sec_liquidity_raw_provenance SET cik='0000009999' WHERE security_id='synthetic-00'", 'industry_classification_unavailable'),
    ("DELETE FROM sec_liquidity_raw_provenance WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'", 'market_cap_unavailable'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"val\":10000000','\"val\":1000000000') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'; UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'", 'market_cap_outside_band'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"val\":10000000','\"val\":1000000') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'; UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'", 'market_cap_outside_band'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"end\":\"2026-07-25\",\"filed\":\"2026-07-28\"','\"end\":\"2024-01-01\",\"filed\":\"2024-01-05\"') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'; UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'", 'market_cap_unavailable'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"filed\":\"2026-07-28\"','\"filed\":\"2026-10-05\"') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'; UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'", 'market_cap_unavailable'),
    ("UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"form\":\"10-Q\"','\"form\":\"8-K\"') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'; UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'", 'market_cap_unavailable'),
])
def test_exact_company_blockers(paths,sql,reason):
    change(paths, sql); r = run(paths); c = company(r)
    assert not c['eligible'] and reason in c['reasons']
    assert c['security_id'] not in r['proposed_membership']
    assert r['withholding_counts'][reason] >= 1


def test_below_ten_withholds_results_without_weakening_gates(paths):
    change(paths, "DELETE FROM corporate_action_coverage_evidence WHERE security_id >= 'synthetic-09'")
    r = run(paths)
    assert r['eligible_count'] == 9 and len(r['proposed_membership']) == 9
    assert r['blockers'] == ['eligible_population_below_minimum'] and not r['results']
    assert r['withholding_counts']['corporate_action_coverage_missing_or_incomplete']==9


def test_duplicate_prices_and_unresolved_discontinuity(paths):
    change(paths, "INSERT INTO global_price_observations SELECT qualified_symbol,trading_date,exchange,currency,open,high,low,close,adjusted_close,volume,status,'other-source',retrieved_at FROM global_price_observations WHERE qualified_symbol='SYN00.US'")
    assert 'duplicate_price_observation' in company(run(paths))['reasons']
    change(paths,"UPDATE global_price_observations SET adjusted_close=adjusted_close*100,close=close*100,open=open*100,high=high*100,low=low*100 WHERE qualified_symbol='SYN01.US' AND trading_date=(SELECT max(trading_date) FROM global_price_observations)")
    assert 'unresolved_price_discontinuity' in company(run(paths),1)['reasons']


def test_actions_are_not_inferred_from_absence(paths):
    change(paths, "INSERT INTO global_corporate_actions VALUES ('SYN00.US',DATE '2026-09-30','dividend',1,'USD','offline-synthetic',TIMESTAMP '2026-09-30 22:00:00')")
    assert 'corporate_action_coverage_event_conflict' in company(run(paths))['reasons']
    change(paths,"UPDATE corporate_action_coverage_evidence SET coverage_state='action_present' WHERE security_id='synthetic-00'")
    assert company(run(paths))['eligible']
    change(paths,"UPDATE global_corporate_actions SET action_type='delisting'")
    assert 'unresolved_corporate_action' in company(run(paths))['reasons']


def test_missing_schema_explicit_and_no_initialization(paths):
    change(paths,'DROP TABLE corporate_action_coverage_evidence')
    r = run(paths)
    assert r['source_schema_states']['corporate_action_coverage_evidence']=='table_absent'
    assert r['eligible_count']==0 and not r['results']
    change(paths,'DROP TABLE security_classification_evidence')
    assert 'visible_ordinary_company_roster_unavailable' in run(paths)['blockers']


def test_direct_conflicts_stale_and_missing_are_unknown(paths):
    change(paths,"UPDATE sec_facts SET period_end=DATE '2020-01-01' WHERE security_id='synthetic-00'")
    c=company(run(paths))
    assert next(m for m in c['missing_data'] if m['field']=='cash_and_cash_equivalents')['reasons']==['stale_direct_evidence']
    change(paths,"INSERT INTO sec_facts SELECT fact_key||'-conflict',security_id,qualified_symbol,ticker,cik,taxonomy,concept,value+1,unit,currency,period_start,period_end,fiscal_year,fiscal_period,frame,form,accession_number,filed_date,public_at,is_amendment,is_revision,source_endpoint,retrieved_at FROM sec_facts WHERE security_id='synthetic-01'")
    c=company(run(paths),1)
    assert next(m for m in c['missing_data'] if m['field']=='cash_and_cash_equivalents')['reasons']==['conflicting_visible_values']


def test_configuration_expansion_and_deterministic_ties(paths):
    default=run(paths); expanded=run(paths,target_members=20)
    assert expanded['proposed_membership'][:15]==default['proposed_membership']
    assert expanded['configuration_hash']!=default['configuration_hash']
    change(paths,'UPDATE global_price_observations SET adjusted_close=110 WHERE trading_date=(SELECT max(trading_date) FROM global_price_observations)')
    assert run(paths)['results']==sorted(default['proposed_membership'])[:3]


def test_limits_future_cutoff_aliases_and_cell_guard(paths,monkeypatch):
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_DATABASE_UNAVAILABLE'):
        service.assess(research_db=paths[0],production_db=paths[0],decision_at=DECISION)
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_INVALID_TIMESTAMP'):
        service.assess(research_db=paths[0],production_db=paths[1],decision_at=DECISION.replace(tzinfo=None))
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_FUTURE_CUTOFF'):
        service.assess(research_db=paths[0],production_db=paths[1],decision_at=datetime.now(timezone.utc)+timedelta(days=1))
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_INVALID_UNIVERSE_SIZE'):run(paths,target_members=9)
    change(paths,"UPDATE security_listings SET company_name=?",['x'*1025])
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_CELL_LIMIT'):run(paths)
    monkeypatch.setattr(service,'MAX_ROWS',10)
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_ROW_LIMIT'):run(paths)


def test_fingerprint_changes(paths,monkeypatch):
    original = service.fingerprint; calls=[]
    def changed(p):
        calls.append(p)
        return original(p) if len(calls)<3 else None
    monkeypatch.setattr(service,'fingerprint',changed)
    with pytest.raises(service.PrototypeError,match='PROTOTYPE_DATABASE_CHANGED'):run(paths)


def test_fixture_refuses_existing_directory_and_real_cli(paths):
    with pytest.raises(FileExistsError): create_fixture(paths[0].parent)
    command=[sys.executable,'-m','app.prototype.cli','--research-db',str(paths[0]),'--production-db',str(paths[1]),'--decision-at',DECISION.isoformat()]
    result=subprocess.run(command,capture_output=True,text=True,check=False)
    assert result.returncode==0
    report=json.loads(result.stdout)
    assert len(report['eligible_roster'])==18 and report['synthetic_fixture']
    assert str(paths[0]) not in result.stdout and str(paths[1]) not in result.stdout
    change(paths,'DELETE FROM corporate_action_coverage_evidence')
    result=subprocess.run(command,capture_output=True,text=True,check=False)
    assert result.returncode==2 and json.loads(result.stdout)['results']==[]


def test_authenticated_api_shortlist_to_detail_and_staging_guard(paths,monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    settings=Settings(database_path=paths[1],research_database_path=paths[0],staging_mode=True,api_token='test-token')
    monkeypatch.setattr(main,'settings',settings); monkeypatch.setattr(api,'get_settings',lambda:settings)
    before=[fingerprint(p) for p in paths]
    with TestClient(main.app) as client:
        params={'decision_at':DECISION.isoformat()}; headers={'Authorization':'Bearer test-token'}
        assert client.get('/api/v1/research/prototype/roster',params=params).status_code==401
        response=client.get('/api/v1/research/prototype/roster',params=params,headers=headers)
        assert response.status_code==200; report=response.json()
        for sid in report['results']:
            response=client.get('/api/v1/research/prototype/companies/'+sid,params=params,headers=headers)
            assert response.status_code==200; detail=response.json()
            assert detail['qualifying_result'] and detail['proposed_member'] and detail['validation_credit']==0
            assert detail['company']==next(c for c in report['companies'] if c['security_id']==sid)
        assert client.get('/api/v1/research/prototype/companies/unknown',params=params,headers=headers).status_code==404
        assert client.post('/api/v1/research/prototype/roster',headers=headers).status_code==409
        assert client.get('/api/v1/research/prototype/roster',params={'decision_at':DECISION.isoformat(),'target_members':9},headers=headers).status_code==422
    assert before==[fingerprint(p) for p in paths]


def test_same_day_sessions_require_completion_and_price_inputs_are_not_future(paths):
    # Make catalogue/evidence visible before the last synthetic session's close.
    change(paths,"UPDATE security_master_retrievals SET retrieved_at=TIMESTAMP '2026-09-30 18:00:00'")
    params=dict(research_db=paths[0],production_db=paths[1])
    early=service.assess(**params,decision_at=datetime(2026,9,30,21,tzinfo=timezone.utc))
    assert early['results']==[]
    assert all('insufficient_visible_exchange_sessions' in c['reasons'] for c in early['companies'])
    complete=service.assess(**params,decision_at=datetime(2026,9,30,22,tzinfo=timezone.utc))
    assert complete['eligible_count']==18
    change(paths,"UPDATE global_price_observations SET retrieved_at=TIMESTAMP '2026-09-29 22:00:00' WHERE qualified_symbol='SYN00.US' AND trading_date=DATE '2026-09-30'")
    assert 'invalid_price_history' in company(run(paths))['reasons']


def test_identity_at_cutoff_without_full_window_mapping_is_eligible_with_explicit_risk(paths):
    change(paths, "DELETE FROM issuer_mapping_candidates WHERE security_id='synthetic-00'")
    c = company(run(paths))
    assert c['eligible'] and c['identity_evidence'] is None
    assert any('ticker reuse' in risk for risk in c['risks'])
    change(paths, "UPDATE security_listings SET cik='0000000100' WHERE security_id='synthetic-00'")
    assert company(run(paths))['eligible']  # an agreeing listing CIK is accepted


def test_session_calendar_is_derived_from_prices_not_weekdays(paths):
    # A holiday-like date with one stray symbol is not a session.
    change(paths, "INSERT INTO global_price_observations SELECT qualified_symbol,DATE '2026-09-27',exchange,currency,open,high,low,close,adjusted_close,volume,status,source,retrieved_at FROM global_price_observations WHERE qualified_symbol='SYN05.US' AND trading_date=DATE '2026-09-25'")
    r = run(paths)
    assert r['session_calendar']['candidate_dates'] == 128 and r['session_calendar']['derived_sessions'] == 127
    assert r['eligible_count'] == 18
    # A real session missing for most symbols drops out, so fewer than 127 remain.
    change(paths, "DELETE FROM global_price_observations WHERE trading_date=DATE '2026-09-29' AND qualified_symbol<>'SYN05.US'")
    r = run(paths)
    assert r['session_calendar']['derived_sessions'] == 126 and r['eligible_count'] == 0
    assert all('insufficient_visible_exchange_sessions' in c['reasons'] for c in r['companies'])


def test_action_present_is_checked_against_its_whole_assessed_interval(paths):
    change(paths, "UPDATE corporate_action_coverage_evidence SET coverage_state='action_present', assessed_from=DATE '2016-01-04' WHERE security_id='synthetic-00'")
    assert 'corporate_action_coverage_event_conflict' in company(run(paths))['reasons']
    change(paths, "INSERT INTO global_corporate_actions VALUES ('SYN00.US',DATE '2020-03-02','cash_distribution',0.5,'USD','offline-synthetic',TIMESTAMP '2020-03-03 00:00:00')")
    c = company(run(paths))
    assert c['eligible'] and c['action_coverage']['coverage_state'] == 'action_present'


def test_quarter_and_year_to_date_at_one_end_are_shown_separately_never_combined(paths):
    for key, start, value in (('q', '2026-04-01', 50), ('ytd', '2026-01-01', 90)):
        change(paths, f"INSERT INTO sec_facts SELECT fact_key||'-{key}',security_id,qualified_symbol,ticker,cik,taxonomy,'Revenues',{value},unit,currency,DATE '{start}',period_end,fiscal_year,fiscal_period,frame,form,accession_number,filed_date,public_at,is_amendment,is_revision,source_endpoint,retrieved_at FROM sec_facts WHERE security_id='synthetic-00'")
    revenue = [f for f in company(run(paths))['direct_evidence'] if f['field'] == 'revenue']
    assert [(f['reported_start'], f['reported_days'], f['value']) for f in revenue] == [('2026-04-01', 91, 50.0), ('2026-01-01', 181, 90.0)]


def test_size_and_industry_are_shown_with_their_inputs(paths):
    c = company(run(paths), 5)
    size, industry = c['size'], c['industry']
    assert size['shares_outstanding'] == 10_000_000 and size['share_classes_summed'] == 1
    assert size['market_cap_usd'] == pytest.approx(10_000_000 * size['close'])
    assert size['close_session'] == c['calculation']['end_session'] and size['band_usd'] == [300_000_000, 10_000_000_000]
    assert industry['sic'] == 3560 and industry['entity_type'] == 'operating' and len(industry['response_sha256']) == 64
    # Two classes in one filing are summed and flagged as a risk.
    second = '{"accn":"0000000105-26-000001","end":"2026-07-25","filed":"2026-07-28","form":"10-Q","val":2000000}'
    change(paths, "UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"val\":10000000}','\"val\":10000000},'||?) WHERE security_id='synthetic-05' AND endpoint_class='companyfacts'", [second])
    change(paths, "UPDATE sec_liquidity_raw_provenance SET response_sha256=sha256(payload_json), byte_count=strlen(payload_json) WHERE endpoint_class='companyfacts'")
    c = company(run(paths), 5)
    assert c['size']['shares_outstanding'] == 12_000_000 and c['size']['share_classes_summed'] == 2
    assert any('share counts' in r for r in c['risks'])


def test_coverage_record_ending_early_is_extended_only_by_a_completed_refresh_after_the_close(paths):
    # Stored record ends 3 sessions before the window end, as after a price refresh.
    change(paths, "UPDATE corporate_action_coverage_evidence SET assessed_to=DATE '2026-09-25' WHERE security_id='synthetic-00'")
    assert 'corporate_action_coverage_missing_or_incomplete' in company(run(paths))['reasons']
    change(paths, "CREATE TABLE eodhd_ingestion_checkpoints(stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP)")
    for status, at, ok in (('pending', '2026-09-30 22:30:00', False),
                           ('completed', '2026-09-30 21:59:00', False),   # before the last session closed
                           ('completed', '2026-10-01 00:30:00', False),   # after the cutoff
                           ('completed', '2026-09-30 22:30:00', True)):
        change(paths, "DELETE FROM eodhd_ingestion_checkpoints")
        change(paths, "INSERT INTO eodhd_ingestion_checkpoints VALUES ('refresh','SYN00.US',?,NULL,?)", [status, at])
        c = company(run(paths))
        assert c['eligible'] is ok, (status, at, c['reasons'])
    assert c['action_coverage']['extension']['stored_record_through'] == '2026-09-25'
    # A dividend after the stored record's end does not contradict verified_no_action ...
    change(paths, "INSERT INTO global_corporate_actions VALUES ('SYN00.US',DATE '2026-09-29','cash_distribution',0.5,'USD','offline-synthetic',TIMESTAMP '2026-09-30 22:00:00')")
    assert company(run(paths))['eligible']
    # ... but one inside the stored record's claimed no-action range does.
    change(paths, "INSERT INTO global_corporate_actions VALUES ('SYN00.US',DATE '2026-09-24','cash_distribution',0.5,'USD','offline-synthetic',TIMESTAMP '2026-09-30 22:00:00')")
    assert 'corporate_action_coverage_event_conflict' in company(run(paths))['reasons']


def test_edited_payload_without_matching_hash_is_not_used(paths):
    change(paths, "UPDATE sec_liquidity_raw_provenance SET payload_json=replace(payload_json,'\"val\":10000000','\"val\":20000000') WHERE security_id='synthetic-00' AND endpoint_class='companyfacts'")
    c = company(run(paths))
    assert 'market_cap_unavailable' in c['reasons'] and c['size'] is None


def test_completed_dividend_queries_count_as_coverage_for_non_payers():
    """No stored coverage record (a company that never paid a dividend): the completed
    10-year prices run and a run after the window's close are the evidence."""
    from datetime import date, datetime, timezone
    from app.prototype.service import _dividend_fetch_coverage
    sec = {'qualified_symbol': 'NOPAY.US'}
    wanted = [date(2026, 4, 9), date(2026, 10, 8)]
    decision = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    def checkpoint(stage, at): return {'qualified_symbol': 'NOPAY.US', 'stage': stage, 'status': 'completed', 'updated_at': at}
    full, refresh = checkpoint('prices', datetime(2026, 9, 27)), checkpoint('refresh', datetime(2026, 10, 8, 23, 30))
    reasons, coverage = _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full, refresh]}, decision, wanted, [])
    assert reasons == [] and coverage['coverage_state'] == 'verified_no_action' and coverage['assessed_to'] == wanted[-1]
    dividend = {'ex_date': date(2026, 6, 1), 'action_type': 'cash_distribution', 'value': 0.1, 'source': 'eodhd'}
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full, refresh]}, decision, wanted, [dividend])[1]['coverage_state'] == 'action_present'
    missing = ['corporate_action_coverage_missing_or_incomplete']
    # Only the refresh (no 10-year run), or nothing after the window closed, or a refresh not yet visible: not covered.
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [refresh]}, decision, wanted, [])[0] == missing
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full]}, decision, wanted, [])[0] == missing
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full, refresh]}, datetime(2026, 10, 8, 23, tzinfo=timezone.utc), wanted, [])[0] == missing
    failed = dict(refresh, status='failed')
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full, failed]}, decision, wanted, [])[0] == missing
    odd = dict(dividend, action_type='spinoff')
    assert _dividend_fetch_coverage(sec, {'eodhd_ingestion_checkpoints': [full, refresh]}, decision, wanted, [odd])[0] == ['unresolved_corporate_action']


def test_identity_matching_equals_the_track_b_rule_beyond_its_roster_bound(monkeypatch):
    """The prototype's own copy of the exact-ID rule agrees with track_b_gaps._reconcile
    (bound lifted) on 300 identities, including every conflict it flags."""
    from datetime import datetime, timezone
    from app import track_b_gaps
    from app.prototype.service import _matched
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decision = datetime(2026, 10, 9, tzinfo=timezone.utc)
    def classification(sid, kind='us_operating_company', cik_value=None):
        return {'security_id': sid, 'security_type': kind, 'cik': cik_value, 'public_at': at, 'retrieved_at': at, 'available_at': at, 'is_current': True}
    data = {k: [] for k in ('security_listings', 'universe_snapshot_members', 'sec_issuers', 'sec_facts', 'canonical_factor_evidence', 'security_classification_evidence')}
    for i in range(300):
        sid, value = f'id-{i:03}', str(1000 + i)
        data['security_classification_evidence'].append(classification(sid, cik_value=value))
        if i % 10 != 9:  # every tenth has no stored identity outside its classification: unmatched
            data['security_listings'].append({'security_id': sid, 'cik': None})
            data['sec_issuers'].append({'security_id': sid, 'cik': value})
    data['security_classification_evidence'].append(classification('id-001', kind='us_fund'))       # conflicting types
    data['sec_facts'].append({'security_id': 'id-002', 'cik': '999999'})                              # second CIK
    data['sec_issuers'].append({'security_id': 'id-004', 'cik': '1003'})                              # shares id-003's CIK
    data['sec_issuers'].append({'security_id': 'id-005', 'cik': 'abc'})                               # invalid CIK
    monkeypatch.setattr(track_b_gaps, 'MAX_ROSTER', 10_000)
    _, expected, _ = track_b_gaps._reconcile(data, decision)
    assert _matched(data, decision) == expected
    assert len(expected) == 300 - 30 - 5 and not {'id-001', 'id-002', 'id-003', 'id-004', 'id-005'} & expected


def test_industry_and_share_counts_are_read_from_retained_sec_documents(tmp_path):
    import hashlib, json
    from datetime import datetime, timezone
    import duckdb
    from app.prototype.service import _industry, _share_counts
    from app.sec_ingestion import SCHEMA
    path = tmp_path / 'r.duckdb'
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    docs = {'submissions': {'sic': '3714', 'sicDescription': 'Motor Vehicle Parts', 'entityType': 'operating', 'name': 'LKQ CORP'},
            'companyfacts': {'facts': {'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
                {'end': '2026-07-20', 'val': 260000000, 'accn': '0000065984-26-000010', 'form': '10-Q', 'filed': '2026-07-25'}]}}}}}}
    with duckdb.connect(str(path)) as db:
        db.execute(SCHEMA)
        for kind, payload in docs.items():
            text = json.dumps(payload, separators=(',', ':'))
            digest = hashlib.sha256(text.encode()).hexdigest()
            db.execute('INSERT INTO sec_raw_payloads VALUES (?,?,?,?,?,?,?,?,?)', [kind, 'sid-1', '0001065696', kind, 'url', at, digest, len(text.encode()), text])
        # A tampered copy is never used.
        db.execute("INSERT INTO sec_raw_payloads VALUES ('bad','sid-2','0000000002','companyfacts','url',?,'0',1,'{}')", [at])
        found, state = _industry(db, datetime(2026, 10, 9, tzinfo=timezone.utc))
        shares = _share_counts(db, datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert state == 'supported' and found['sid-1'][0]['sic'] == '3714' and found['sid-1'][0]['cik'] == '0001065696'
    assert [e['value'] for e in shares['sid-1']] == [260000000.0] and 'sid-2' not in shares


def test_batched_fact_and_price_reads_match_one_company_at_a_time(paths, monkeypatch):
    params = dict(research_db=paths[0], production_db=paths[1], decision_at=DECISION)
    monkeypatch.setattr(service, 'FACT_BATCH', 1)
    single = json.dumps(service.assess(**params), sort_keys=True)
    monkeypatch.setattr(service, 'FACT_BATCH', 4)
    assert json.dumps(service.assess(**params), sort_keys=True) == single
