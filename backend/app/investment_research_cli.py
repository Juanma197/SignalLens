"""Deterministic JSON CLI for Milestone 36 read-only reports."""
import argparse, json, sys
from datetime import datetime
from pathlib import Path
from .investment_research import (coverage_audit, repair_plan, execution_cost_capability,
                                   research_readiness, public_error_code)

COMMANDS={"investment-grade-coverage-audit":coverage_audit,
          "plan-investment-data-repair":repair_plan,
          "execution-cost-capability":execution_cost_capability,
          "undervalued-quality-research-readiness":research_readiness}
def parser():
    root=argparse.ArgumentParser(description="Read-only investment research foundation")
    subs=root.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        p=subs.add_parser(name); p.add_argument("--research-db",required=True,type=Path)
        p.add_argument("--production-db",required=True,type=Path); p.add_argument("--decision-at",required=True,type=datetime.fromisoformat)
    return root
def main():
    args=parser().parse_args()
    try: print(json.dumps(COMMANDS[args.command](research_db=args.research_db,production_db=args.production_db,decision_at=args.decision_at),sort_keys=True,default=str))
    except Exception as exc:
        print(json.dumps({"status":"failed","error":{"code":public_error_code(exc),"message":"investment research request failed; details redacted"}},sort_keys=True),file=sys.stderr); raise SystemExit(1) from None
if __name__=="__main__": main()
