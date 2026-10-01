"""Operator CLI for Milestone 33 research synchronization."""
import argparse, json
from pathlib import Path
from .research_sync import apply, plan, rollback, status

def parser():
    p=argparse.ArgumentParser(); p.add_argument("command", choices=("plan-research-sync","apply-research-sync","research-sync-status","rollback-research-sync"))
    for name in ("candidate","research","production","bootstrap","backup-dir"):
        p.add_argument("--"+name, type=Path)
    p.add_argument("--expected-sha256"); p.add_argument("--expected-byte-count",type=int)
    p.add_argument("--plan-id"); p.add_argument("--authorization"); p.add_argument("--quiescence-seconds",type=float,default=2)
    return p

def execute(a):
    if not a.research or not a.production: raise ValueError("explicit --research and --production paths required")
    if a.command=="research-sync-status":
        if not a.backup_dir: raise ValueError("explicit --backup-dir required")
        return status(a.research,a.production,backup_dir=a.backup_dir)
    if not a.candidate or not a.expected_sha256 or a.expected_byte_count is None: raise ValueError("explicit candidate hash and size required")
    if a.command=="plan-research-sync":
        if not a.bootstrap: raise ValueError("explicit --bootstrap required")
        return plan(a.candidate,a.research,a.production,bootstrap=a.bootstrap,expected_sha256=a.expected_sha256,expected_byte_count=a.expected_byte_count)
    if not a.backup_dir: raise ValueError("explicit --backup-dir required")
    if a.command=="apply-research-sync":
        if not a.bootstrap or not a.plan_id: raise ValueError("explicit bootstrap and plan ID required")
        return apply(a.candidate,a.research,a.production,bootstrap=a.bootstrap,backup_dir=a.backup_dir,expected_sha256=a.expected_sha256,expected_byte_count=a.expected_byte_count,plan_id=a.plan_id,authorization=a.authorization or "",quiescence_seconds=a.quiescence_seconds)
    return rollback(a.candidate,a.research,a.production,backup_dir=a.backup_dir,expected_sha256=a.expected_sha256,expected_byte_count=a.expected_byte_count,authorization=a.authorization or "",quiescence_seconds=a.quiescence_seconds)

def main():
    try: print(json.dumps(execute(parser().parse_args()),sort_keys=True,default=str))
    except Exception as exc: raise SystemExit(f"research synchronization refused: {type(exc).__name__}: {exc}") from None
if __name__=="__main__": main()
