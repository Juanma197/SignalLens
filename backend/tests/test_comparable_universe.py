from datetime import datetime, timedelta, timezone
import pytest

from app.comparable_universe import *

NOW=datetime(2026,9,30,tzinfo=timezone.utc)
def evidence(kind, **extra):
    return {"security_type":kind,"durable_identifier":"CIK:0001","source":"sec_filing",
            "public_at":NOW-timedelta(days=2),"retrieved_at":NOW-timedelta(days=1)}|extra

@pytest.mark.parametrize("kind",["bank","insurance","reit","bdc","investment_fund","spac","spac_unit","foreign_issuer_or_adr","pre_revenue_development_stage","other_special_structure"])
def test_special_types_are_explicitly_excluded(kind):
    result=classify_security([evidence(kind)],NOW)
    assert result.security_type==kind and not result.included and result.reason_code

def test_ordinary_classification_future_and_ambiguity_refusal():
    assert classify_security([evidence(ORDINARY)],NOW).included
    assert classify_security([evidence(ORDINARY),evidence("bank")],NOW).reason_code=="classification_ambiguous"
    future=evidence(ORDINARY,public_at=NOW+timedelta(seconds=1))
    assert classify_security([future],NOW).security_type=="classification_unavailable"

def test_durable_effective_dated_mapping_and_ticker_reuse():
    identity={"security_id":"s1","cik":"0001","effective_from":NOW-timedelta(days=1),"effective_to":None}
    stored={"security_id":"s1","cik":"0001","known_at":NOW-timedelta(hours=1)}
    assert repair_issuer_mapping(security_id="s1",decision_at=NOW,listing_identities=[identity],stored_mappings=[stored])["status"]=="mapped"
    assert repair_issuer_mapping(security_id="s1",decision_at=NOW,listing_identities=[identity],stored_mappings=[])["status"]=="mapping_available_not_persisted"
    reused=identity|{"cik":"0002"}
    assert repair_issuer_mapping(security_id="s1",decision_at=NOW,listing_identities=[identity,reused],stored_mappings=[])["status"]=="ambiguous_mapping"

def test_alias_contracts_accept_semantics_not_similar_names():
    good={"concept":"ShortTermDebtCurrent","unit_kind":"monetary","period_nature":"instant","sign":"positive","currency":"USD"}
    assert accept_alias("current_debt",good)
    assert not accept_alias("current_debt",good|{"concept":"DebtMaybeCurrent"})
    assert accept_alias("cash",{"concept":"CashAndCashEquivalentsAtCarryingValue","unit_kind":"monetary","period_nature":"instant","sign":"positive","currency":"USD"})
    assert accept_alias("interest_expense",{"concept":"InterestAndDebtExpense","unit_kind":"monetary","period_nature":"duration","sign":"expense","currency":"USD"})

def test_value_factors_sign_denominators_and_optional_ev():
    assert earnings_yield(10,100)["value"]==.1
    assert earnings_yield(-10,100)["negative_earnings"]
    assert earnings_yield(1,1e-12)["status"]=="withheld"
    assert fcf_yield(20,5,100)["free_cash_flow"]==15
    assert fcf_yield(20,-5,100,capex_sign="negative_cash_flow")["free_cash_flow"]==15
    assert book_to_market(50,100)["value"]==.5
    assert book_to_market(-1,100)["status"]=="withheld"
    assert construct_enterprise_value(100,None,10)["reason_code"]=="debt_evidence_unavailable"
    debt=[{"value":20,"double_count_group":"current"},{"value":30,"double_count_group":"noncurrent"}]
    assert construct_enterprise_value(100,debt,10)["value"]==140
    assert construct_enterprise_value(100,debt+[debt[0]],10)["reason_code"]=="debt_component_double_count"
    # EV failure is independent of valid market-cap calculations.
    assert earnings_yield(10,100)["status"]=="available"

def test_financial_strength_debt_free_missing_negative_equity_and_interest():
    missing=financial_strength(assets=100,equity=50)
    assert missing["debt_free_status"]=="unknown"
    result=financial_strength(current_debt=10,non_current_debt=20,assets=100,equity=50,cash=5,operating_income=12,interest_expense=3)
    assert result["debt_to_assets"]["value"]==.3 and result["debt_to_equity"]["value"]==.6
    assert result["interest_coverage"]["value"]==4
    assert financial_strength(current_debt=0,non_current_debt=0,confirmed_debt_free=True,assets=1,equity=-1,interest_expense=0)["debt_to_equity"]["status"]=="withheld"
    assert financial_strength(current_debt=0,non_current_debt=0,assets=1,equity=1,interest_expense=0)["interest_coverage"]["status"]=="withheld"
    assert financial_strength(security_type="bank")["reason_code"]=="financial_sector_not_comparable"

@pytest.mark.parametrize("args,state,passes",[
 ({"assessed":True,"valid_at_decision":True,"events":[]},"verified_no_corporate_action",True),
 ({"assessed":False,"valid_at_decision":True,"events":[]},"missing_coverage",False),
 ({"assessed":True,"valid_at_decision":True,"events":[{"type":"split"}]},"stored_corporate_action",True),
 ({"assessed":True,"valid_at_decision":True,"events":[],"unresolved":True},"unresolved_corporate_action",False)])
def test_corporate_action_states(args,state,passes):
    assert corporate_action_coverage(**args)==({"state":state,"passes":passes,"event_count":0} if state=="verified_no_corporate_action" else
      {"state":state,"passes":passes,"event_count":1} if state=="stored_corporate_action" else {"state":state,"passes":passes})

def test_repair_aggregates_before_bounded_output():
    items=[{"field":"cash","reason_code":"unsupported_taxonomy","disposition":"unavailable_with_current_evidence","security_type":ORDINARY,"estimated_provider_requests":1} for _ in range(120)]
    report=aggregate_repair_plan(items,10)
    assert report["total_work_item_count"]==120 and report["counts_by_field"]=={"cash":120}
    assert report["displayed_work_item_count"]==10 and report["truncated"] and report["total_estimated_requests"]==120
