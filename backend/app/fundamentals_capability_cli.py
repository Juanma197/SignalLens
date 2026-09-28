"""CLI for the Milestone 17 fundamentals capability assessment."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from .config import get_settings
from .fundamentals_capability import CapabilityLimits, FundamentalsCapabilityAssessment

DEFAULT_FIXTURE = Path(__file__).parents[1] / "tests/fixtures/fundamentals_capability.json"

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bounded point-in-time fundamentals capability assessment")
    p.add_argument("fundamentals-capability", choices=["fundamentals-capability"])
    p.add_argument("--fixture", type=Path, nargs="?", const=DEFAULT_FIXTURE)
    p.add_argument("--authorize-live", action="store_true")
    p.add_argument("--max-requests", type=int, default=5); p.add_argument("--timeout-seconds", type=float, default=10)
    p.add_argument("--pacing-seconds", type=float, default=1); p.add_argument("--max-response-bytes", type=int, default=5_000_000)
    p.add_argument("--production-db", type=Path); p.add_argument("--research-db", type=Path)
    return p

def execute(a: argparse.Namespace) -> dict:
    settings = get_settings()
    if a.fixture is None and not a.authorize_live: raise ValueError("live probe requires --authorize-live")
    fixtures = json.loads(a.fixture.read_text()) if a.fixture else None
    assessment = FundamentalsCapabilityAssessment(os.getenv("SIGNALLENS_EODHD_API_TOKEN") if fixtures is None else None,
        limits=CapabilityLimits(max_requests=a.max_requests, timeout_seconds=a.timeout_seconds,
                                pacing_seconds=a.pacing_seconds, max_response_bytes=a.max_response_bytes))
    return assessment.run(database_paths=[a.production_db or settings.database_path,
        a.research_db or settings.research_database_path], fixtures=fixtures)

def main() -> None:
    try: print(json.dumps(execute(parser().parse_args()), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"command":"fundamentals-capability", "status":"failed",
            "error":{"code":type(exc).__name__, "message":"capability assessment failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None
if __name__ == "__main__": main()
