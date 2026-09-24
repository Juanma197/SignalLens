# Global investable-universe operations

Milestone 7 adds **research/shadow infrastructure** for point-in-time listing
discovery. It does not feed the live ranking, replace `app.universe.UNIVERSE`,
publish a prediction vintage, or run in the production monthly scheduler.

## Sources and licensing boundaries

The built-in US discovery provider downloads the exchange-operated Nasdaq Trader
Symbol Directory (`nasdaqlisted.txt` and `otherlisted.txt`). It is listing-level
reference data: it is not a consolidated global security master, does not provide
complete domicile/ISIN/LEI coverage, and may not identify ultimate issuer
relationships. Review the provider's current terms before operational use.

UK, Canadian, Euronext, Xetra, SIX, Nasdaq Nordic, Italian, and Spanish exchange
coverage uses the provider-neutral reference CSV contract. An operator must obtain
an exchange-operated or appropriately licensed file and explicitly pass it to the
command. SignalLens does not scrape exchange web pages, bypass access controls,
ship proprietary data, or fall back to a hand-maintained ticker list. Some complete
exchange reference feeds are commercial. Missing feeds remain visibly unavailable.

Required CSV columns are:

```text
source_key,ticker,exchange,company_name,listing_country,currency,instrument_type
```

Optional columns are `domicile`, `is_primary`, `active`, `isin`, `cik`, `lei`, and
`exchange_symbol`. Identifiers must be included only where the operator has a
lawful right to use them. Expected initial scale is roughly 12,000–18,000 raw
listings and 8,000–12,000 canonical companies, but recorded retrieval counts—not
these estimates—are authoritative.

## Safe commands

All commands emit one structured JSON document. Preview parses and summarizes a
provider without opening DuckDB:

```bash
cd backend
python -m app.global_universe_cli preview
python -m app.global_universe_cli preview --provider lse --reference-file /approved/lse.csv
```

Writing is always explicit. Refresh creates an immutable, content-addressed
retrieval; an identical retry returns `already_exists`:

```bash
python -m app.global_universe_cli refresh
python -m app.global_universe_cli refresh --provider tmx --reference-file /approved/tmx.csv
```

Create one immutable snapshot per calendar month and inspect coverage:

```bash
python -m app.global_universe_cli snapshot --month 2026-09-01 --reporting-currency GBP
python -m app.global_universe_cli status
```

Snapshot creation never substitutes current constituents for a historical
retrieval: it selects the latest completed retrieval whose timestamp is no later
than the snapshot timestamp and stores its retrieval ID and source timestamp.
Attempting to change an existing month's content is rejected.

## Identity, canonical listing, and exclusions

Stable company grouping uses LEI, then CIK, then an exact ISIN, then a
normalized company name plus domicile/country. Tickers are never global identity;
symbols are exchange-qualified. Every raw normalized listing is retained.
Canonical selection is deterministic: active, explicitly primary, ordinary/common,
home-country, metadata-complete listings sort ahead of alternatives, followed by
exchange, ticker, and source keys as stable tie-breakers. This handles ADRs,
secondary listings, share-class alternatives, and ticker collisions without
discarding source records. Imperfect provider identity data can still under- or
over-group issuers, so the stored evidence and decisions remain auditable.

Every evaluated listing stores all applicable reasons, including noncanonical or
secondary listing, instrument type, OTC/invalid exchange, inactive/delisted,
invalid metadata, missing/stale provider data, insufficient history, price,
liquidity, and unavailable FX.

Defaults are ordinary/common equity, USD 5 adjusted close, USD 5 million median
daily value over 60 trading days, and 126 valid history days. Price and liquidity
metrics are intentionally not backfilled from the live 30-stock pipeline in this
milestone; until global point-in-time price coverage is supplied, snapshots record
`missing_price_data` rather than claiming eligibility.

## Currency and point-in-time safety

Local prices and currencies remain unchanged. `fx_observations` stores explicit
base/quote currency, observation date, retrieval timestamp, source, and rate.
`StoredFXProvider` only returns observations and retrievals available at the
requested point in time. It never uses a current rate for history. GBP is the
default reporting currency, but USD threshold comparison for non-USD securities
is marked `fx_unavailable` until a lawful, reliable FX ingestion is implemented.

## API and production separation

`GET /api/v1/universe/coverage` is read-only and remains protected by the existing
Bearer authentication policy. The dashboard labels it shadow research and reports
listing/company/eligibility totals, exclusions, country/exchange/currency coverage,
missing/stale inputs, and the latest snapshot.

Do not add these commands to `monthly_cycle`, attach another DuckDB writer, or use
them against production without the existing backup/preflight operating controls.
The September 2026 vintage and `momentum_126d` production path remain untouched.

## Milestone 8 price, action, FX, and calendar contract

Milestone 8 adds validated CSV adapters rather than pretending that an unofficial
website is an institutional data licence. Nasdaq Trader's exchange-operated symbol
directory remains the only built-in automated listing feed. LSE, TMX (TSX/TSXV),
Euronext, Deutsche Börse/Xetra, SIX, Nasdaq Nordic, Borsa Italiana, and BME files
must be obtained and used under the operator's terms. Those regions report
**unavailable** until a file is supplied. Yahoo/yfinance is an unofficial convenience
provider: its existing 30-stock production use is unchanged, but it is not claimed
as authoritative global listing or price coverage. Scraping exchange sites, bypassing
access controls, and automating sources whose terms prohibit it are unsupported.

Price CSV columns are `qualified_symbol,trading_date,exchange,currency,open,high,low,
close,adjusted_close,volume`; optional `status` is `available`, `missing`, `stale`, or
`failed`. Corporate-action columns are `qualified_symbol,ex_date,action_type,value,
currency`, where the type is `split` or `cash_distribution`. FX columns are
`base_currency,quote_currency,observed_on,rate,available_at`. Use an approved
point-in-time source; official ECB or Bank of England reference data may be suitable
only after the operator confirms current terms, supported pairs, publication timing,
and transformation. The adapter does not invent direct pairs or availability times.

```bash
cd backend
python -m app.global_market_data_cli import-prices --source licensed_vendor \
  --price-file /approved/prices.csv --actions-file /approved/actions.csv --dry-run
python -m app.global_market_data_cli import-fx --source approved_reference_rates \
  --fx-file /approved/fx.csv --dry-run
python -m app.global_market_data_cli status
```

Dry-run parses and validates without initializing or writing the database. Natural
keys make imports idempotent. Programmatic providers use per-run item limits,
batches, retries, inter-batch rate limits, maximum duration, checkpoints, and
structured failures. Defaults are 50 symbols per batch, 500 per run, three attempts,
and 15 minutes; choose lower provider/region limits where terms require them.

Local OHLC, volume, adjusted close, splits, and distributions are preserved.
Conversion uses only an FX observation whose `available_at` was known by query time
and whose observation is no more than four calendar days old. Missing/stale FX stays
missing; there is no indefinite forward fill or current-FX historical conversion.
GBX divides by 100 to produce GBP. Calendars accept venue-specific holiday sets.

At planning scale, 10,000 securities × ten years × 252 sessions is about 25 million
rows. Budget roughly 25–50 GB before DuckDB compression. Development uses only
representative fixtures; no bulk ingestion is performed.

`GET /api/v1/universe/market-data-coverage` is authenticated and read-only. It
reports price coverage by exchange/currency, trading/retrieval freshness, FX
freshness, and latest run failures. The research eligibility report exposes metadata,
history, liquidity, FX, momentum-history, and explicit exclusion flags without
publishing a production vintage.
