"""Structured JSON interface for isolated Milestone 10 workflows."""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .research_evaluation import (ResearchEvaluationRepository, create_manifest,
    leakage_findings, evaluate_gates)

def parser():
    root=argparse.ArgumentParser(); root.add_argument("--research-db",type=Path,required=True)
    sub=root.add_subparsers(dest="command",required=True)
    for name in ("dataset-audit","controlled-import","point-in-time-build","robustness-analysis","report-export"):
        p=sub.add_parser(name); p.add_argument("--input",type=Path); p.add_argument("--dry-run",action="store_true")
        p.add_argument("--checkpoint",type=Path); p.add_argument("--resume",action="store_true")
    p=sub.add_parser("manifest-create"); p.add_argument("--file",type=Path,action="append",required=True); p.add_argument("--version",required=True); p.add_argument("--output",type=Path); p.add_argument("--dry-run",action="store_true")
    p=sub.add_parser("leakage-audit"); p.add_argument("--input",type=Path,required=True)
    p=sub.add_parser("walk-forward-evaluate"); p.add_argument("--metrics",type=Path,required=True); p.add_argument("--evaluation-id",required=True); p.add_argument("--model-version",required=True); p.add_argument("--dry-run",action="store_true")
    p=sub.add_parser("shadow-ranking"); p.add_argument("--evaluation-id",required=True); p.add_argument("--candidates",type=Path,required=True); p.add_argument("--evaluated-at",type=datetime.fromisoformat,required=True); p.add_argument("--dry-run",action="store_true")
    sub.add_parser("status")
    return root

def execute(args, now=None):
    now=now or datetime.now(timezone.utc); repo=ResearchEvaluationRepository(args.research_db)
    if args.command=="status": return {"command":args.command,**repo.status()}
    if args.command=="manifest-create":
        commit=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,check=True).stdout.strip()
        result=create_manifest(args.file,dataset_version=args.version,configuration={"monthly":True},code_commit=commit,retrieved_at=now,coverage={"limitations":["operator coverage not independently inferred"]})
        if not args.dry_run:
            if args.output: args.output.write_bytes(json.dumps(result,sort_keys=True,indent=2).encode()+b"\n")
            repo.store_manifest(result)
        return {"command":args.command,"status":"validated" if args.dry_run else "completed","mutated":not args.dry_run,"manifest":result}
    if args.command=="leakage-audit":
        findings=leakage_findings(pd.read_csv(args.input)); return {"command":args.command,"status":"failed" if findings else "passed","findings":findings}
    if args.command=="walk-forward-evaluate":
        metrics=json.loads(args.metrics.read_text()); gates=evaluate_gates(metrics)
        if not args.dry_run: gates=repo.store_evaluation(args.evaluation_id,args.model_version,metrics)
        return {"command":args.command,"status":"passed" if gates["passed"] else "research_only","mutated":not args.dry_run,"gates":gates}
    if args.command=="shadow-ranking":
        candidates=json.loads(args.candidates.read_text())
        if args.dry_run: return {"command":args.command,"status":"validated","mutated":False,"candidates":len(candidates)}
        return {"command":args.command,**repo.create_shadow(args.evaluation_id,args.evaluated_at,candidates)}
    # Staged operations are explicit validation contracts until operator data exists.
    rows=0 if not args.input else sum(1 for _ in args.input.open("rb"))
    return {"command":args.command,"status":"validated","mutated":False,"rows_or_lines":rows,
            "checkpoint":str(args.checkpoint) if args.checkpoint else "not_started",
            "resume":args.resume,"message":"No operator dataset was imported"}

def main():
    try: print(json.dumps(execute(parser().parse_args()),sort_keys=True,default=str))
    except Exception as exc: print(json.dumps({"status":"failed","error":{"code":type(exc).__name__,"message":str(exc)}}),file=sys.stderr); raise SystemExit(1) from None
if __name__=="__main__": main()
