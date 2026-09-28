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
    assert result['feature_classifications']['current_summary_ratios']=='latest_only_not_backtestable'
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

@pytest.mark.parametrize('value',[0,6])
def test_request_budget_validation(value):
    with pytest.raises(ValueError,match='max_requests'): CapabilityLimits(max_requests=value)
