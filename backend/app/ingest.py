from argparse import ArgumentParser
from datetime import date

from .config import get_settings
from .market_data import MarketDataRepository, YFinanceProvider
from .universe import UNIVERSE


def main() -> None:
    parser = ArgumentParser(description="Ingest SignalLens daily market data")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    repository = MarketDataRepository(get_settings().database_path)
    run_id = repository.ingest(YFinanceProvider(), UNIVERSE, args.start, args.end)
    print(f"Completed ingestion run {run_id}")


if __name__ == "__main__":
    main()
