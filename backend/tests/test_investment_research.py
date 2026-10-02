from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.investment_research import (CONFIGURATION_HASH, CostAssumptions, REASON_CODES,
    TRACK_B_LABELS, total_return, validate_alias)

def test_track_a_frozen_and_track_b_has_required_boundaries():
    assert CONFIGURATION_HASH == "7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd"
    assert TRACK_B_LABELS == ["TRACK B RESEARCH FOUNDATION — NOT A MODEL",
        "NO PURCHASE RECOMMENDATION", "NO CANDIDATES GENERATED", "ZERO VALIDATION CREDIT"]

def test_alias_requires_complete_semantic_compatibility():
    good={"concept":"Revenues","unit":"USD","duration":"duration","sign":"credit","meaning":"revenue"}
    assert validate_alias("revenue",good)
    for key,bad in (("unit","shares"),("duration","instant"),("sign","debit"),("meaning","gross profit")):
        changed=good|{key:bad}; assert not validate_alias("revenue",changed)
    assert not validate_alias("revenue",good|{"concept":"SalesRevenueNet"})

def test_cost_hash_deterministic_and_costs_never_silently_zero():
    a=CostAssumptions(); b=CostAssumptions()
    assert a.configuration_hash == b.configuration_hash
    result=total_return(start_price=100,end_price=110,cash_dividends=2,assumptions=a)
    assert result["gross_total_return"] == pytest.approx(.12)
    assert result["net_total_return"] < result["gross_total_return"]
    assert result["cost_fraction"] == pytest.approx(.005)
    assert result["spread_source"] == "estimated_conservative"

def test_observed_spread_commission_fx_slippage_and_denominator_withholding():
    assumptions=CostAssumptions(commission_bps=4,fx_bps=6,slippage_bps=8,turnover=.5)
    result=total_return(start_price=10,end_price=11,cash_dividends=.1,assumptions=assumptions,spread_bps=12)
    assert result["cost_fraction"] == pytest.approx((12+4+6+8)*.5/10000)
    assert result["spread_source"] == "observed"
    assert total_return(start_price=0,end_price=1,cash_dividends=0,assumptions=assumptions) == {"status":"withheld","reason_code":"unreliable_denominator"}

def test_reason_codes_are_stable_bounded_and_spec_has_no_weights():
    assert len(REASON_CODES) == len(set(REASON_CODES)) and len(REASON_CODES) < 50
    spec=json.loads((Path(__file__).parents[1]/"app/undervalued_quality_draft_v0.1.0.json").read_text())
    assert spec["weights"] is None
    assert len(spec["factors"]) == 6
    assert "prospective evidence after registration" in spec["future_registration_requirements"]
