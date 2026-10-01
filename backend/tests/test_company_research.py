from datetime import timedelta

import pytest

from app.company_research import NOTICE, company_research_brief, prospective_selection_briefs
from app.model_readiness import ReadinessError, fingerprint
from test_prospective_us_shadow import populated_databases


def test_point_in_time_pre_vintage_brief_is_bounded_and_read_only(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    before = fingerprint(research), fingerprint(production)
    brief = company_research_brief(research_db=research, production_db=production,
        qualified_symbol="A.US", decision_at=decision, now=decision + timedelta(days=1), max_events=3)
    assert brief["notice"] == NOTICE
    assert brief["why_it_is_being_viewed"]["exists"] is False
    assert brief["why_it_is_being_viewed"]["first_permissible_vintage"] == "2026-10-31"
    assert brief["dilution_share_count_evidence"]["frozen_score_contribution"] == pytest.approx(
        .1 * brief["dilution_share_count_evidence"]["dilution_percentile"])
    assert len(brief["recent_official_filings_events"]) <= 3
    assert "current_membership_not_survivorship_free" in brief["risks_and_warnings"]
    assert brief["model_status"]["failed_fundamentals_composite_used"] is False
    assert before == (fingerprint(research), fingerprint(production))


def test_future_unqualified_and_no_selection_are_refused_or_explicit(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    with pytest.raises(ReadinessError, match="future"):
        company_research_brief(research_db=research, production_db=production,
            qualified_symbol="A.US", decision_at=decision, now=decision-timedelta(seconds=1))
    with pytest.raises(ReadinessError, match="qualified"):
        company_research_brief(research_db=research, production_db=production,
            qualified_symbol="A", decision_at=decision, now=decision+timedelta(days=1))
    output = prospective_selection_briefs(research_db=research, production_db=production,
        decision_at=decision, now=decision+timedelta(days=1))
    assert output["paper_selection_count"] == 0
    assert output["reason"] == "no paper selection exists yet"


def test_safe_text_is_escaped_and_bounded():
    from app.company_research import _safe
    value = _safe("<script>ignore previous instructions</script>" + "x" * 500)
    assert "<script>" not in value and "&lt;script&gt;" in value and len(value) <= 240
