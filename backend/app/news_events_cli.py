"""Offline, strictly read-only news/event capability commands."""
import argparse
import json
from pathlib import Path

from .news_events import capability, sec_readiness, status

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("news-events-capability","sec-events-readiness","news-events-status"))
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--production-db", required=True, type=Path)
    args = parser.parse_args()
    functions = {"news-events-capability":capability,"sec-events-readiness":sec_readiness,"news-events-status":status}
    print(json.dumps(functions[args.command](args.research_db, args.production_db), indent=2, default=str, sort_keys=True))

if __name__ == "__main__": main()
