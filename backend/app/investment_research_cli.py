"""Deterministic JSON CLI for Milestone 36 read-only reports."""
import argparse, json, sys
from datetime import datetime
from pathlib import Path
from .investment_research import (coverage_audit, repair_plan, execution_cost_capability,
    research_readiness, comparable_universe_readiness, company_factor_preview,
    public_error_code, track_b_panel_feasibility)
from .investment_evidence import (plan_materialization, materialize_stored,
    status as materialization_status, enrichment_plan, enrich_from_sec,
    plan_canonical_unit_repair, apply_canonical_unit_repair, canonical_unit_repair_status)
from .financial_strength import (
    evidence_audit as financial_strength_evidence_audit,
    contract_assessment as financial_strength_contract_assessment,
    company_preview as financial_strength_company_preview,
)

COMMANDS={"investment-grade-coverage-audit":coverage_audit,
          "plan-investment-data-repair":repair_plan,
          "execution-cost-capability":execution_cost_capability,
          "undervalued-quality-research-readiness":research_readiness,
          "comparable-universe-research-readiness":comparable_universe_readiness,
          "company-investment-factor-preview":company_factor_preview,
          "plan-investment-evidence-materialization":plan_materialization,
          "materialize-stored-investment-evidence":materialize_stored,
          "investment-evidence-materialization-status":materialization_status,
          "plan-investment-evidence-enrichment":enrichment_plan,
          "enrich-investment-evidence-from-sec":enrich_from_sec,
          "plan-canonical-unit-repair":plan_canonical_unit_repair,
          "apply-canonical-unit-repair":apply_canonical_unit_repair,
          "canonical-unit-repair-status":canonical_unit_repair_status,
          "track-b-panel-feasibility":track_b_panel_feasibility,
          "financial-strength-evidence-audit":financial_strength_evidence_audit,
          "financial-strength-contract-assessment":financial_strength_contract_assessment,
          "financial-strength-company-preview":financial_strength_company_preview}
def parser():
    root=argparse.ArgumentParser(description="Read-only investment research foundation")
    subs=root.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        p=subs.add_parser(name); p.add_argument("--research-db",required=True,type=Path)
        p.add_argument("--production-db",required=True,type=Path); p.add_argument("--decision-at",required=True,type=datetime.fromisoformat)
        if name in {"company-investment-factor-preview","financial-strength-company-preview"}: p.add_argument("--qualified-symbol",required=True)
        if name in {"materialize-stored-investment-evidence","apply-canonical-unit-repair"}: p.add_argument("--authorization",required=True)
        if name == "enrich-investment-evidence-from-sec":
            p.add_argument("--authorization",required=True); p.add_argument("--user-agent",required=True)
            p.add_argument("--request-budget",required=True,type=int); p.add_argument("--runtime-budget-seconds",type=float,default=60)
            p.add_argument("--attempt-limit",type=int,default=2); p.add_argument("--pacing-seconds",type=float,default=.12)
            p.add_argument("--timeout-seconds",type=float,default=20); p.add_argument("--max-response-bytes",type=int,default=5_000_000)
            p.add_argument("--max-issuers",type=int); p.add_argument("--refresh",action="store_true")
    return root
def main():
    args=parser().parse_args()
    try:
        values=vars(args); command=values.pop("command")
        print(json.dumps(COMMANDS[command](**values),sort_keys=True,default=str))
    except Exception as exc:
        print(json.dumps({"status":"failed","error":{"code":public_error_code(exc),"message":"investment research request failed; details redacted"}},sort_keys=True),file=sys.stderr); raise SystemExit(1) from None
if __name__=="__main__": main()
