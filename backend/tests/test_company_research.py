from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from app.company_research import (NOTICE, CompanyEvidenceUnavailableError,
                                  InvalidQualifiedSymbolError, company_research_brief,
                                  prospective_selection_briefs)
from app.company_research_cli import _reason_code
from app.model_readiness import ReadinessError, fingerprint
from app.prospective_us_shadow import (AUTHORIZATION_PHRASE, create_from_database_plan,
                                       plan_from_databases)
from app.sec_events import SCHEMA as EVENT_SCHEMA
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


def test_operator_shaped_october_pre_vintage_uses_only_visible_evidence(tmp_path):
    research, production, _ = populated_databases(tmp_path)
    decision = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        # Make the fixture's completed 127-session history end on September 30.
        db.execute("UPDATE global_price_observations SET trading_date=trading_date-INTERVAL 30 DAY, retrieved_at=retrieved_at-INTERVAL 30 DAY")
        db.execute("UPDATE global_fx_observations SET observed_on=observed_on-INTERVAL 30 DAY, available_at=available_at-INTERVAL 30 DAY, retrieved_at=retrieved_at-INTERVAL 30 DAY")
        db.execute(EVENT_SCHEMA)
        values = ["visible", "hash-visible", "a", "A.US", "0000000001", "acc-visible",
            "8-K", datetime(2026, 9, 30).date(), datetime(2026, 9, 30, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 30).date(), '["3.02"]', "visible.htm", "ref", None,
            "synthetic", datetime(2026, 9, 30, 11, tzinfo=timezone.utc), "mapped", 1.0,
            "capital_raise", "fixture", "public", "research", "current report", False]
        db.execute("INSERT INTO sec_event_metadata VALUES (" + ",".join("?" for _ in values) + ")", values)
        future = list(values); future[0] = "future"; future[1] = "hash-future"; future[5] = "acc-future"
        future[8] = datetime(2026, 10, 2, tzinfo=timezone.utc); future[15] = datetime(2026, 10, 2, tzinfo=timezone.utc)
        db.execute("INSERT INTO sec_event_metadata VALUES (" + ",".join("?" for _ in future) + ")", future)
    before = fingerprint(research), fingerprint(production)
    brief = company_research_brief(research_db=research, production_db=production,
        qualified_symbol="A.US", decision_at=decision, now=decision + timedelta(minutes=1))
    assert brief["why_it_is_being_viewed"]["exists"] is False
    assert brief["why_it_is_being_viewed"]["reason"] == "no paper selection exists yet"
    assert brief["price_behaviour"]["history_quality"] == "model-ready"
    assert [event["citation"]["accession"] for event in brief["recent_official_filings_events"]] == ["acc-visible"]
    assert not any(word in brief["why_it_is_being_viewed"]["reason"].lower()
                   for word in ("selected", "ranked", "recommended", "top 3"))
    assert before == (fingerprint(research), fingerprint(production))


def test_unknown_symbol_and_missing_dilution_are_explicit(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    with pytest.raises(ReadinessError, match="unknown"):
        company_research_brief(research_db=research, production_db=production,
            qualified_symbol="UNKNOWN.US", decision_at=decision, now=decision + timedelta(minutes=1))
    with duckdb.connect(str(research)) as db:
        db.execute("DELETE FROM sec_facts WHERE security_id='a'")
    brief = company_research_brief(research_db=research, production_db=production,
        qualified_symbol="A.US", decision_at=decision, now=decision + timedelta(minutes=1))
    dilution = brief["dilution_share_count_evidence"]
    assert dilution["staleness_or_withholding"] == "dilution_evidence_unavailable"
    assert dilution["dilution_percentile"] is None and dilution["frozen_score_contribution"] is None
    assert brief["model_status"]["score_withheld"] is True
    assert "dilution_score_evidence" in brief["missing_information"]


def test_prospective_database_plan_remains_strictly_month_end(tmp_path):
    research, production, _ = populated_databases(tmp_path)
    decision = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)
    with pytest.raises(ReadinessError, match="complete month-end"):
        plan_from_databases(research_db=research, production_db=production,
            decision_at=decision, session_date=decision.date(), now=decision)


def test_registered_vintage_contributions_are_reconciled_exactly(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    plan = plan_from_databases(research_db=research, production_db=production,
        decision_at=decision, session_date=decision.date(), now=decision)
    create_from_database_plan(research_db=research, production_db=production,
        plan_identifier=plan["plan_identifier"], authorization=AUTHORIZATION_PHRASE,
        now=decision + timedelta(minutes=1))
    stored = next(row for row in plan["proposed_paper_selections"] if row["qualified_symbol"] == "A.US")
    viewed = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    brief = company_research_brief(research_db=research, production_db=production,
        qualified_symbol="A.US", decision_at=viewed, now=viewed + timedelta(minutes=1))
    status = brief["why_it_is_being_viewed"]
    assert status["exists"] is True and status["vintage_exists"] is True
    assert status["final_frozen_score"] == stored["prospective_score"]
    assert brief["price_behaviour"]["price_contribution"] == pytest.approx(.9 * stored["price_percentile"])
    assert brief["dilution_share_count_evidence"]["frozen_score_contribution"] == pytest.approx(
        .1 * stored["dilution_percentile"])


def test_cli_reason_codes_are_stable_and_do_not_reflect_details():
    assert _reason_code(InvalidQualifiedSymbolError("details")) == "COMPANY_BRIEF_INVALID_SYMBOL"
    assert _reason_code(CompanyEvidenceUnavailableError("details")) == "COMPANY_BRIEF_EVIDENCE_UNAVAILABLE"
    assert _reason_code(ReadinessError("unknown, ambiguous, or non-model-ready qualified symbol")) == "COMPANY_BRIEF_EVIDENCE_UNAVAILABLE"
    assert _reason_code(RuntimeError("provider raw response")) == "COMPANY_BRIEF_INTERNAL_ERROR"


def test_cli_symbol_reason_codes_cover_malformed_unknown_unscored_and_success(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    common = {"research_db": research, "production_db": production,
              "decision_at": decision, "now": decision + timedelta(minutes=1)}

    with pytest.raises(InvalidQualifiedSymbolError) as malformed:
        company_research_brief(**common, qualified_symbol="a")
    assert _reason_code(malformed.value) == "COMPANY_BRIEF_INVALID_SYMBOL"

    with pytest.raises(CompanyEvidenceUnavailableError) as unknown:
        company_research_brief(**common, qualified_symbol="UNKNOWN.US")
    assert _reason_code(unknown.value) == "COMPANY_BRIEF_EVIDENCE_UNAVAILABLE"

    # A.US remains an eligible member of the active catalogue, but no longer
    # has enough history to enter frozen price-score reconstruction.
    with duckdb.connect(str(research)) as db:
        db.execute("""DELETE FROM global_price_observations
                      WHERE qualified_symbol='A.US' AND trading_date < DATE '2026-10-30'""")
        active_count = db.execute("""SELECT count(*) FROM security_listings
            WHERE retrieval_id='r' AND qualified_symbol='A.US' AND active""").fetchone()[0]
    assert active_count == 1
    with pytest.raises(CompanyEvidenceUnavailableError) as unscored:
        company_research_brief(**common, qualified_symbol="A.US")
    assert _reason_code(unscored.value) == "COMPANY_BRIEF_EVIDENCE_UNAVAILABLE"

    brief = company_research_brief(**common, qualified_symbol="B.US")
    assert brief["company_identity"]["qualified_symbol"] == "B.US"
