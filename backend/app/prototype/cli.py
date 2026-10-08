"""Read-only actual roster review. No approval, freeze or write command exists."""
import argparse
from datetime import datetime
import json

from .service import PrototypeError, assess


def main():
    parser = argparse.ArgumentParser(description='Review the unfrozen unvalidated prototype roster from stored evidence only.')
    parser.add_argument('--research-db', required=True)
    parser.add_argument('--production-db', required=True)
    parser.add_argument('--decision-at', required=True, type=datetime.fromisoformat)
    parser.add_argument('--target-members', type=int, default=15)
    args = parser.parse_args()
    try:
        report = assess(research_db=args.research_db, production_db=args.production_db,
                        decision_at=args.decision_at, target_members=args.target_members)
    except PrototypeError as exc:
        print(json.dumps({'code': exc.code, 'validation_credit': 0, 'provider_requests': 0, 'writes': 0}))
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 2 if report['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
