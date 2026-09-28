from __future__ import annotations
import json
from pathlib import Path
import httpx
import pytest
from app.fundamentals_capability import (CapabilityLimits, FundamentalsCapabilityAssessment,
    normalize_document, records_as_of)
from app.fundamentals_capability_cli import execute, parser

FIXTURE=Path(__file__).with_name('fixtures')/'fundamentals_capability.json'

def doc(records, statement='Income_Statement', periodicity='quarterly'):
    return {'Financials':{statement:{periodicity:records}}}

def test_filing_boundary_and_fiscal_end_never_implies_availability():
    rows=normalize_document(doc({'a':{'date':'2023-12-31','filing_date':'2024-02-15','totalRevenue':'1'},
                                 'b':{'date':'2024-03-31','totalRevenue':'2'}}),'US')
    assert records_as_of(rows,'2024-02-14') == []
    assert len(records_as_of(rows,'2024-02-15')) == 1
    assert rows[1]['available_at'] is None and rows[1] not in records_as_of(rows,'2030-01-01')

def test_restatement_only_replaces_original_after_publication():
    rows=normalize_document(doc([
      {'date':'2023-12-31','filing_date':'2024-02-01','netIncome':'10'},
      {'date':'2023-12-31','filing_date':'2024-04-01','netIncome':'8'}]),'US')
    assert records_as_of(rows,'2024-03-01')[0]['values']['netIncome']=='10'
    assert records_as_of(rows,'2024-04-01')[0]['values']['netIncome']=='8'

def test_annual_quarterly_currency_units_and_duplicate_periods_remain_explicit():
    payload={'Financials':{'Income_Statement':{
      'quarterly':[{'date':'2023-12-31','filingDate':'2024-02-01','currency_symbol':'GBX','totalRevenue':'100'},
                   {'date':'2023-12-31','filingDate':'2024-02-01','currency_symbol':'GBP','totalRevenue':'1'}],
      'yearly':{'x':{'date':'2023-12-31','acceptedDate':'2024-03-01','currency_symbol':'GBP','totalRevenue':'4'}}}}}
    rows=normalize_document(payload,'LSE')
    assert {r['periodicity'] for r in rows}=={'quarterly','yearly'}
    assert {r['currency'] for r in rows}=={'GBP','GBX'}
    # Duplicate/reported units are not silently summed; a deterministic revision wins.
    assert len(records_as_of(rows,'2024-02-01')) == 1

def test_current_summary_is_excluded_and_fixture_output_is_sanitized(tmp_path):
    fixtures=json.loads(FIXTURE.read_text()); fixtures['US']['Highlights']['SecretRatio']=123
    result=FundamentalsCapabilityAssessment().run(database_paths=[tmp_path/'p',tmp_path/'r'],fixtures=fixtures)
    rendered=json.dumps(result)
    assert 'SecretRatio' not in rendered and 'MarketCapitalization' not in rendered and 'REDACTED SYNTHETIC' not in rendered
    assert result['theoretical_schema_classification']['current_summary_ratios']=='latest_only_not_backtestable'
    assert result['live_entitlement_classification']=='not_assessed'
    assert result['confirmed_live_feature_classification']['revenue_growth']=='not_assessed'
    assert result['aggregate']['point_in_time_reconstruction'] is True

def test_database_immutability_and_offline_cli(tmp_path):
    p,r=tmp_path/'p.db',tmp_path/'r.db'; p.write_bytes(b'prod'); r.write_bytes(b'research')
    args=parser().parse_args(['fundamentals-capability','--fixture',str(FIXTURE),'--production-db',str(p),'--research-db',str(r)])
    result=execute(args)
    assert result['request_count']==0 and result['database_immutability']['verified']
    assert p.read_bytes()==b'prod' and r.read_bytes()==b'research'

def test_strict_request_budget_with_retries(tmp_path):
    calls=[]
    def handler(request): calls.append(request); return httpx.Response(429, text='provider token=SECRET details')
    a=FundamentalsCapabilityAssessment('SECRET',limits=CapabilityLimits(max_requests=3,max_attempts=2,pacing_seconds=0),
       transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=a.run(database_paths=[tmp_path/'p',tmp_path/'r'])
    assert result['request_count']==3==len(calls)
    assert 'SECRET' not in json.dumps(result) and 'provider token' not in json.dumps(result)
    assert result['status']=='provider_access_unavailable'
    assert result['confirmed_live_feature_classification']['revenue_growth']=='unavailable'

@pytest.mark.parametrize(('status','expected'),[(401,'authentication_failed'),(403,'subscription_restricted')])
def test_authentication_and_subscription_http_statuses_are_distinct(tmp_path,status,expected):
    def handler(request): return httpx.Response(status, text='token=TOPSECRET provider private detail')
    assessment=FundamentalsCapabilityAssessment('TOPSECRET',limits=CapabilityLimits(max_attempts=1,pacing_seconds=0),
        transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=assessment.run(database_paths=[tmp_path/'p',tmp_path/'r'])
    assert all(region['request_diagnostic']['classification']==expected for region in result['regions'])
    assert result['status']=='provider_access_unavailable'
    assert 'TOPSECRET' not in json.dumps(result) and 'private detail' not in json.dumps(result)

def test_http_200_subscription_error_is_safely_classified_and_redacted(tmp_path):
    secret='acct@example.test requires Fundamentals subscription; token=HUSH'
    def handler(request): return httpx.Response(200,json={'error':secret,'account_identity':'customer-42'})
    assessment=FundamentalsCapabilityAssessment('HUSH',limits=CapabilityLimits(max_attempts=1,pacing_seconds=0),
        transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=assessment.run(database_paths=[tmp_path/'p',tmp_path/'r'])
    rendered=json.dumps(result)
    diagnostic=result['regions'][0]['request_diagnostic']
    assert diagnostic['classification']=='subscription_restricted'
    assert diagnostic['top_level_field_names']==['error']
    assert secret not in rendered and 'customer-42' not in rendered and 'HUSH' not in rendered

@pytest.mark.parametrize(('payload','expected'),[
    ([], 'empty_payload'),
    ({'General':{'Code':'AAPL'}}, 'schema_mismatch'),
])
def test_empty_and_schema_mismatch_are_distinct(tmp_path,payload,expected):
    def handler(request): return httpx.Response(200,json=payload,headers={'content-type':'application/json; charset=utf-8'})
    assessment=FundamentalsCapabilityAssessment('secret',limits=CapabilityLimits(max_requests=1,max_attempts=1,pacing_seconds=0),
        transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=assessment.run(database_paths=[tmp_path/'p',tmp_path/'r'],diagnostic_one_request=True)
    diagnostic=result['regions'][0]['request_diagnostic']
    assert diagnostic['classification']==expected
    assert diagnostic['content_type']=='application/json' and diagnostic['response_bytes'] is not None

def test_valid_fundamentals_payload_is_available(tmp_path):
    payload=doc({'a':{'date':'2023-12-31','filing_date':'2024-02-15','totalRevenue':'1'}})
    def handler(request): return httpx.Response(200,json=payload)
    assessment=FundamentalsCapabilityAssessment('secret',limits=CapabilityLimits(max_requests=1,max_attempts=1,pacing_seconds=0),
        transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=assessment.run(database_paths=[tmp_path/'p',tmp_path/'r'],diagnostic_one_request=True)
    assert result['request_count']==1 and result['scope']['security_count']==1
    assert result['regions'][0]['request_diagnostic']['classification']=='available'
    assert result['confirmed_live_feature_classification']['revenue_growth']=='derivable_with_constraints'

def test_malformed_json_does_not_expose_body(tmp_path):
    def handler(request): return httpx.Response(200,text='not-json token=BODYSECRET')
    assessment=FundamentalsCapabilityAssessment('URLSECRET',limits=CapabilityLimits(max_requests=1,max_attempts=1,pacing_seconds=0),
        transport=httpx.MockTransport(handler),sleep=lambda _:None)
    result=assessment.run(database_paths=[tmp_path/'p',tmp_path/'r'],diagnostic_one_request=True)
    rendered=json.dumps(result)
    assert result['regions'][0]['request_diagnostic']['classification']=='malformed_json'
    assert 'BODYSECRET' not in rendered and 'URLSECRET' not in rendered

def test_cli_exposes_explicit_one_request_mode(tmp_path):
    args=parser().parse_args(['fundamentals-capability','--authorize-live','--diagnostic-one-request',
        '--production-db',str(tmp_path/'p'),'--research-db',str(tmp_path/'r')])
    assert args.diagnostic_one_request and args.max_requests==5

@pytest.mark.parametrize('value',[0,6])
def test_request_budget_validation(value):
    with pytest.raises(ValueError,match='max_requests'): CapabilityLimits(max_requests=value)
