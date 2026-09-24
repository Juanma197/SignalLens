# Milestone 10 controlled research evaluation

## Scope and honest coverage

No investable global dataset is committed or bundled. At implementation time the
repository contained only a small listing test fixture: zero historical memberships,
delistings, global prices, FX observations, fundamental facts, benchmarks, or
evaluation vintages. Consequently **no performance result and no current shadow
ranking is claimed**. The intended operator sample is monthly 2016–2026 for ordinary,
liquid US, UK, Canadian, and developed-European equities, evaluated separately by
region; an aggregate must be labelled incomplete whenever any region lacks historical
membership and delistings.

Raw/licensed files belong outside Git, conventionally under
`/data/research-import/<provider>/<retrieval>/`. Record every file in the manifest;
Git ignores DuckDB databases and `backend/data/`. Budget approximately 5–20 GB for
normalized data and 10–50 GB for staged raw files. Runtime can range from hours to a
day and must follow provider limits. Checkpoints are provider/operator artifacts and
must be retained beside inputs.

## Source and licensing register

| Purpose | Provider / documentation | Authority and permitted use | Authentication / limits | Depth, coverage, revisions, gaps |
|---|---|---|---|---|
| US listings | [Nasdaq Trader Symbol Directory](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs) | Exchange-operated public reference files; review terms before operational reuse | None documented by the adapter; download politely | Current Nasdaq/other US listings; not historical membership, delistings, or authoritative issuer identity |
| US listings/delistings | Approved CRSP/Nasdaq/NYSE vendor file | Licensed authoritative vendor data; only within operator licence | Vendor credentials and contractual limits | Required for defensible historical membership/delistings; unavailable in repository |
| UK | LSE or licensed vendor reference/history | Exchange/vendor authority; contractual use only | Usually credentials/licence | No supplied history; unavailable |
| Canada | TMX or licensed vendor reference/history | Exchange/vendor authority; contractual use only | Usually credentials/licence | No supplied history; unavailable |
| Developed Europe | Euronext, Deutsche Börse, SIX, Nasdaq Nordic, Borsa Italiana/BME or licensed vendor | Exchange/vendor authority; contractual use only | Venue-specific | No supplied history; unavailable; venue/country coverage must be declared |
| Prices/actions/benchmarks | Licensed vendor CSV contract | Use only within licence; raw files never committed | Vendor-specific | Required for robust adjustments, delistings, and index proxies. yfinance is unofficial, revision-prone, current-symbol biased, and not a completeness claim |
| FX to GBP | [ECB data](https://data.ecb.europa.eu/) or [Bank of England IUD](https://www.bankofengland.co.uk/boeapps/database/) / licensed feed | Official reference rates subject to stated terms | Usually no key; source limits apply | Business-day reference observations, not executable quotes; point-in-time retrieval required; missing currencies require licensed feed |
| US filings/fundamentals | [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Official public disclosure data under SEC fair-access policy | Descriptive user agent; currently 10 requests/second maximum | US filers; amendments and taxonomy mappings revise facts; non-US issuer gaps and no normalized consensus estimates |
| Non-US fundamentals | Approved filings/vendor operator files | Regulator/exchange documents or licensed normalization | Jurisdiction/vendor-specific | Publication timestamps, restatements, and taxonomy mappings required; unavailable in repository |
| Macro | [FRED](https://fred.stlouisfed.org/docs/api/fred/) / ALFRED | Official aggregator; API key for API route | FRED limits apply | FRED latest values may revise; ALFRED vintages required for leakage-safe macro history |
| Catalysts/news | Google News RSS / [GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) | Public metadata discovery, not article-content licence | Service limits; no bypassing controls | Coverage/ranking can change; incomplete archives and no validated sentiment |

Providers may omit share classes, inactive names, actions, volume, announcement
timestamps, taxonomy extensions, index membership, or executable FX. Never scrape an
access-controlled/prohibited source and never infer absent fields.

## Point-in-time and evaluation protocol

At each monthly decision timestamp select only a previously captured immutable
universe snapshot. Require `available_at` and `retrieved_at` no later than the cutoff
for prices, FX, filings, fundamentals, and catalyst metadata. Use venue sessions,
execute at the next open/session observation, retain delisted outcomes, and record
missing/stale states and deterministic hashes. `leakage-audit` fails future universe,
price, or feature availability and same-session execution.

Compare production `momentum_126d`, fixed and constrained-learned multifactor scores,
equal weight, and supplied regional/global proxies at 21/63/126 sessions. Use nested
expanding windows; choose horizons without the untouched test period. Report local and
GBP returns, sample counts, top-one/top-three excess and hit rates, rank correlation,
annualized return/volatility, drawdown, turnover, costs, deterministic bootstrap
intervals, and year/region/sector/regime/missingness/coefficient breakdowns. Apply
commissions, FX costs, liquidity constraints and 50 bps UK buy stamp duty as an explicit
research assumption (operators must validate exemptions and current tax rules).

## Predetermined gates and shadow publication

Before results are inspected: ≥60 monthly vintages; ≥100 securities; ≥3 regions with
≥20 each; ≤20% missingness; coefficient drift ≤0.25; annual turnover ≤8×; drawdown
≤35%; positive lower 95% bootstrap bounds for after-cost top-one and top-three excess;
positive after-cost excess; region, sector, and regime robustness; zero leakage;
largest-security contribution ≤25%; untouched forward test; complete historical
membership and delistings. These are research gates, not production approval.

The shadow command writes only immutable `research_current_shadow_vintages` in the
separate research database and can persist zero candidates. It is withheld if any
gate fails. Current freshness/confidence checks must additionally be represented in
the evaluated metrics/operator report. Global model promotion remains prohibited
while membership, delistings, regional coverage, or licensed inputs are incomplete.

## Operator runbook

All commands emit structured JSON. `--research-db` is mandatory and must not equal the
production path. Dry runs never initialize or mutate DuckDB.

```bash
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb dataset-audit --input /data/research-import/inventory.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb manifest-create --file /data/research-import/vendor/prices.parquet --version m10-v1 --output /data/research/manifests/m10-v1.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb controlled-import --input /data/research/manifests/m10-v1.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb point-in-time-build --input build.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb leakage-audit --input audit.csv
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb walk-forward-evaluate --metrics metrics.json --evaluation-id m10-e1 --model-version constrained-v1 --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb robustness-analysis --input metrics.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb shadow-ranking --evaluation-id m10-e1 --candidates candidates.json --evaluated-at 2026-09-24T16:30:00+00:00 --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb report-export --input report.json --dry-run
python -m app.research_evaluation_cli --research-db /data/research/m10.duckdb status
```

Do not access Railway production, deploy, run bulk production imports, change the
scheduler, publish `prediction_vintages`, or describe outputs as investment advice.
