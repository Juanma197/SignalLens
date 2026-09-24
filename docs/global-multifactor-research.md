# Global multifactor shadow research

Milestone 9 is a **research-only** pipeline. It never publishes to
`prediction_vintages`; `momentum_126d` remains the only production publisher.

## Sources, access, and licensing

| Region | Supported route | Limitation |
|---|---|---|
| US | SEC EDGAR Company Facts and approved operator files | Configure a descriptive SEC user agent; taxonomy extensions and amendments need explicit mappings. |
| UK | Validated operator files derived from lawfully obtained Companies House/FCA/issuer iXBRL | No automated scraping. Coverage is unavailable until an approved file is supplied. |
| Canada | Validated operator files from licensed/approved SEDAR+ material | No dependable unrestricted bulk API is assumed. Coverage remains unavailable without supplied data. |
| Europe | Validated operator files from OAM/ESEF or issuer iXBRL | Access and reuse terms vary by jurisdiction; no site scraping is implemented. |

Operators are responsible for permission to use supplied files and must retain the
source document URI and provider. The adapter does not bypass access controls.

## Point-in-time contract

Raw facts retain accounting periods, publication, availability and retrieval times,
currency, unit, accounting standard, source document, provider, provenance, state,
and revision lineage. Normalized facts are separate and preserve their raw `fact_id`.
An as-of query requires both `available_at <= cutoff` and `retrieved_at <= cutoff`.
Later amendments supersede rather than overwrite earlier evidence.

States are `available`, `missing`, `stale`, `unsupported`, and `invalid`. Missing
values are never converted to zero. Currency conversion requires a contemporaneous
Milestone 8 FX observation. Market cap is contemporaneous price × point-in-time
shares; enterprise value, ROE, ROIC, debt ratios, interest coverage, stability,
growth and margins are emitted only when their required inputs and defensible
denominators exist. Otherwise the metric remains explicitly unavailable.

## Features and comparability

- Momentum uses 1/3/6/12-month local and GBP returns, one-month reversal control,
  volatility scaling, drawdown and gap checks.
- Value uses earnings/FCF yields, book-to-market and EV ratios only where meaningful.
- Quality uses profitability, margins, cash conversion, accruals, balance-sheet
  strength, stability and dilution.
- Catalyst uses acceleration, filing/event recency and only lawfully available
  point-in-time surprises, revisions, news, and source agreement.
- Risk covers liquidity, volatility, drawdown, leverage/distress, concentrated
  events, evidence gaps/staleness, currency exposure and abnormal price gaps. It is
  only a penalty, confidence reduction, or hard gate.

Cross-sections use the population known at the evaluation date, 5th/95th percentile
winsorisation, a configurable minimum peer count, and this fallback hierarchy:
industry/region/standard; sector/region/standard; region/standard; standard. Raw
ratios from incompatible industries are not directly compared.

## Models and missing data

The fixed baseline weights momentum 30%, value 25%, quality 25%, and catalyst 20%.
They are declared research assumptions, not an accuracy claim. Risk is subtracted
separately. Feature contributions, evidence, exclusions and confidence are stored.
Confidence combines coverage (70%) and freshness (30%) and is reduced by risk.
Candidates below 55% confidence or a hard gate are excluded; fewer than three—or no
candidate—is valid.

The optional learned model uses positive ridge coefficients, a 60% coefficient cap,
normalisation and shrinkage toward the baseline. Training rows must strictly precede
the outer evaluation cutoff and require at least 12 distinct historical periods.

## Walk-forward protocol

For every monthly cutoff, reconstruct the historical snapshot and features from
facts, prices, FX, memberships and calendars available by that instant. Fit learned
weights only inside an earlier nested window, freeze them, and execute on the next
venue session. Compare production `momentum_126d`, the fixed baseline, and learned
model against appropriate global/regional benchmarks. Report top-one/top-three
returns, hit/rank correlation, excess return, drawdown, volatility, turnover, costs,
FX effects, regional/sector/regime slices, coverage, missingness and confidence
calibration. Delistings remain in their historical population. Promotion is not
approved unless adequate out-of-sample improvement and coefficient stability exist.

## Commands

All output is structured JSON:

```bash
python -m app.global_research_cli fundamentals-validate --file facts.csv --source approved-provider
python -m app.global_research_cli fundamentals-import --file facts.csv --source approved-provider
python -m app.global_research_cli features --feature-file features.csv
python -m app.global_research_cli evaluate --scored-file walk-forward.csv --cost-bps 10
python -m app.global_research_cli shadow-vintage --feature-file features.csv --universe-snapshot-id ID --evaluated-at 2026-09-24T16:30:00+00:00
python -m app.global_research_cli status
```

Validation parses entirely in memory and never opens the database, making it
byte-for-byte non-mutating. Imports are explicit transactions. Shadow vintages are
content-addressed and immutable. The authenticated API exposes only read endpoints.

## Capacity and isolation

Storage grows linearly with facts/features/vintages; raw documents are referenced,
not duplicated. A bounded monthly universe should score in seconds to minutes;
licensed full-history global data may require multiple GB and a separate capacity
review. No bulk live ingestion is part of this milestone. Scheduler, Railway,
backups, fixed production universe, September 2026 vintage, production tables and
official cards are unchanged. Production promotion remains incomplete.
