# Milestone 15 read-only model-readiness runbook

## Scope and truthfulness

`model-readiness` is an aggregate assessment of the **current** five-region
research catalogue. It is research-only, is not survivorship-free, and is not
investment advice. It creates no score, ranking, Top 3, highest-conviction
candidate, model table, output table, database, WAL, or checkpoint. It makes no
provider request and does not use the production publisher.

The adapter opens only the research DuckDB, using DuckDB's read-only mode. The
production path is used solely for path-alias protection and byte fingerprinting.
Both files are fingerprinted before and after with existence, byte count, and
SHA-256, and each `unchanged` result must be `true`.

The command selects the latest completed catalogue retrieval visible at the
decision boundary, loads schema-compatible research rows, then calls the
Milestone 14 transformation. It does not reimplement eligibility. Consequently,
GBP remains an identity conversion, GBX is divided by 100, and USD/CAD/EUR use
only historical GBP rates observed on or before each price date and available by
the decision time.

## Windows PowerShell operation

After merging the PR, open PowerShell at the repository root. Substitute your
real, distinct paths; do not copy the database into the repository:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli model-readiness `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
```

To pin the observation boundary for reproducibility, add an ISO-8601 value with
an explicit UTC offset:

```powershell
--decision-at "2026-09-28T20:00:00+00:00"
```

The JSON report includes selected, loaded, model-ready and withheld totals;
region and currency counts; exclusions; price depth/freshness; FX coverage and
point-in-time semantics; permanent/retryable failures; invalid/duplicate checks;
corporate-action validation; and no more than ten sanitized affected symbols.
It explicitly states that no ranking, Top 3, or highest-conviction candidate was
generated.

## Refusals and operator checks

The command exits nonzero rather than guessing when the research file is missing,
the two paths resolve to the same file (including hard links), the schema is
missing/incompatible, the latest catalogue was not validated, database metadata
cannot be read, or an eligible pilot region is absent. A missing path is never
created. Before relying on a report, confirm:

1. every `database_fingerprints.*.unchanged` field is `true`;
2. `required_regions` contains LSE, PA, TO, US and XETRA;
3. exclusions and withheld counts are acceptable for the intended research; and
4. the explicit no-ranking fields remain `false`.

Fixture results prove only implementation behavior. They are not evidence about
the operator-held research database or live market coverage.
