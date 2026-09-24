"""Structured commands for bounded, research-only multifactor workflows."""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .config import get_settings
from .global_research import GlobalResearchRepository, parse_operator_facts, score_candidates
from .multifactor_backtest import multifactor_score_backtest

def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(description="Global multifactor shadow research")
    sub=root.add_subparsers(dest="command",required=True)
    validate=sub.add_parser("fundamentals-validate"); validate.add_argument("--file",type=Path,required=True); validate.add_argument("--source",required=True)
    imp=sub.add_parser("fundamentals-import"); imp.add_argument("--file",type=Path,required=True); imp.add_argument("--source",required=True)
    features=sub.add_parser("features"); features.add_argument("--feature-file",type=Path,required=True)
    evaluate=sub.add_parser("evaluate"); evaluate.add_argument("--scored-file",type=Path,required=True); evaluate.add_argument("--cost-bps",type=float,default=10)
    vintage=sub.add_parser("shadow-vintage"); vintage.add_argument("--feature-file",type=Path,required=True); vintage.add_argument("--universe-snapshot-id",required=True); vintage.add_argument("--evaluated-at",type=datetime.fromisoformat,required=True)
    sub.add_parser("status")
    return root

def execute(args: argparse.Namespace, now: datetime|None=None) -> dict:
    now=now or datetime.now(timezone.utc); repo=GlobalResearchRepository(get_settings().database_path)
    if args.command=="status": return {"command":"research_status",**repo.status()}
    if args.command.startswith("fundamentals-"):
        rows=parse_operator_facts(args.file.read_text(encoding="utf-8-sig"),source_provider=args.source,retrieved_at=now)
        if args.command=="fundamentals-validate": return {"command":args.command,"status":"validated","facts":len(rows),"mutated":False}
        return repo.import_facts(rows)
    if args.command in {"features","shadow-vintage"}:
        scored=score_candidates(pd.read_csv(args.feature_file))
        if args.command=="features":
            return {"command":"features","status":"completed","candidates":json.loads(scored.to_json(orient="records"))}
        if args.evaluated_at.tzinfo is None: raise ValueError("evaluated-at must include a timezone")
        return repo.create_vintage(universe_snapshot_id=args.universe_snapshot_id,evaluated_at=args.evaluated_at,scored=scored)
    result=multifactor_score_backtest(pd.read_csv(args.scored_file),transaction_cost_bps_per_side=args.cost_bps)
    return {"command":"evaluate","status":"completed","comparison":["momentum_126d","equal_weight_multifactor","constrained_learned_multifactor"],"metrics":result.summary,"promotion_decision":"not_approved"}

def main() -> None:
    try: print(json.dumps(execute(parser().parse_args()),default=str,sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status":"failed","error":{"code":type(exc).__name__,"message":str(exc)}}),file=sys.stderr); raise SystemExit(1) from None
if __name__=="__main__": main()
