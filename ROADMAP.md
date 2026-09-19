# SignalLens Roadmap

Each milestone must end with passing tests, a production frontend build, an updated `STATUS.md`, and a Git checkpoint.

## Milestone 1 — Application foundation (complete)

- Monorepo structure
- FastAPI backend and Next.js frontend
- Health check and demo ranking contract
- Windows startup and test scripts
- Documentation and Git baseline

## Milestone 2 — Research data foundation

- Define investable universe and ranking horizon
- Store reproducible market data in DuckDB
- Adjust prices for splits and distributions
- Validate gaps, duplicates, coverage, and freshness
- Expose data status in the dashboard

## Milestone 3 — Honest baseline model

- Point-in-time features with no look-ahead leakage
- Walk-forward evaluation
- Baseline comparison and transaction-cost assumptions
- Prediction vintages stored immutably

## Milestone 4 — Ranking experience

- Monthly top-one/top-three view
- Evidence, risks, uncertainty, and model explanation
- Predicted-versus-actual tracking
- Watchlist and research notes

## Milestone 5 — Broader evidence

- Fundamentals, filings, news, macro data, and lawful public disclosures
- Source timestamps and provenance
- Missing-data and stale-data handling

## Milestone 6 — Private deployment

- Authentication and secrets management
- Continuous integration
- Private GitHub repository
- Managed frontend, API, and database deployment
- Monitoring, backups, and scheduled ranking jobs

## Guardrails

- Never describe a rank as guaranteed growth.
- Never train on information unavailable at prediction time.
- Keep research results separate from real-money execution.
- Preserve every published prediction so performance cannot be rewritten later.

