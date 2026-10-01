# Milestone 30 — point-in-time news and company events

## Boundary

News is context-first because current price and fundamental hypotheses have not
been validated and language evidence has different timestamp, licensing, identity,
and revision risks. It cannot alter the registered prospective US model: **90%
price and 10% point-in-time dilution**. No event creates a ranking, candidate,
shadow selection, recommendation, publication, or vintage.

## Point-in-time and copyright rules

- Publication, occurrence, announcement, and retrieval are distinct times.
- Publication and retrieval must be timezone-aware. Missing publication time is
  refused for historical evaluation. Both must be no later than the decision time.
- An update time does not establish when original content was known. A current
  page never proves historical availability.
- Corrections and amendments are new, linked versions; earlier versions are not
  overwritten. SEC accessions and 8-K/A forms remain attributable.
- Store only permitted metadata, attribution, canonical URLs, bounded summaries
  (600 characters), and structured classifications—not full articles or provider
  payloads. HTML and controls are sanitized. Provider text is evidence, never an
  instruction. Credentials must never enter records or output.

## Normalized coverage and classification

The schema covers earnings, guidance, M&A/disposals, management, capital raises
and buybacks, dividends, material contracts, litigation/regulation, distress,
products, licensed analyst ratings, government contracts, insider transactions,
lawful timestamped attributable political/government trading, and general news.
It preserves durable issuer IDs, category, bounded text, attribution, all times,
external/accession IDs, correction links, language, jurisdiction, provenance,
license class, hashes, and issuer-match confidence.

Deterministic typed outputs reserve direction (positive/negative/mixed/unknown),
materiality evidence, novelty, authority, freshness, and affected horizon. These
are not LLM ground truth. A future language-model explanation must remain outside
the score and cite the structured source evidence behind every claim.

## Source capability matrix

| Family | Regions/depth | Timestamp/version | IDs/access/limits | Cost/license | Backtest / live | Access |
|---|---|---|---|---|---|---|
| Official regulatory filings | Jurisdiction-specific; SEC US store | Acceptance time; amendments/accessions | CIK; official API/bulk; authority limits | Free/public-record terms | Strong if acceptance retained / potential | **Confirmed offline only** |
| Issuer IR | Global, issuer-dependent; inconsistent archives | Often dated; corrections inconsistent | Weak IDs; feeds/pages/vendor; site limits | Free–paid; site terms | Archives required / theoretical | Theoretical |
| Exchange/regulatory services | Exchange-specific; service depth | Usually authoritative; explicit corrections common | Exchange ID/ISIN; feed/API; contract limits | Free–paid; redistribution limits | Potentially strong / theoretical | Theoretical |
| Government contract/enforcement | Agency/jurisdiction-specific | Dates common, exact time varies; agency revisions | Entity/contract IDs; API/bulk; agency limits | Generally free; public-record terms | Conditional on time/match / theoretical | Theoretical |
| Licensed news APIs | Plan-specific global/depth | Supplied but semantics/versioning require audit | Vendor IDs/API/contract limits | Paid; strict storage/display rules | Contract audit required / theoretical | Theoretical |
| Open web/aggregators | Broad, uneven, unstable archives | Often unreliable; weak corrections | Usually no durable IDs; service limits | Free–paid; copyright/site terms | Generally unsuitable / context-only | Theoretical |
| Yahoo Finance | Broad current context; historical news unproven | Historical timestamp/version semantics unproven | Ticker-centric; reuse risk; access unproven | Terms/storage rights unproven | **Unsuitable for backtests; current context only** | Theoretical |

Free official sources can offer authoritative metadata but uneven history and
entity linkage. Paid feeds may improve coverage and timestamps, but entitlement
does not automatically permit historical storage, display, or derived reuse.
Non-US authority, identifiers, time semantics, language, licensing, and archive
depth remain unconfirmed.

## SEC zero-network capability

`sec-events-readiness` reads existing `sec_filings` metadata for 8-K and 8-K/A,
preserving accession, form, public time, source, retrieval, and amendment status.
Categories are bounded to filing item metadata when present; otherwise the filing
is only general company news. It never fabricates article prose or sentiment and
does not change SEC facts.

## Offline operations

All three commands require distinct explicit database paths, open both databases
read-only, fingerprint both before and after, bound aggregate output, and return
zero rankings, candidates, and selections:

```bash
cd backend
python -m app.news_events_cli news-events-capability --research-db /absolute/research.duckdb --production-db /absolute/production.duckdb
python -m app.news_events_cli sec-events-readiness --research-db /absolute/research.duckdb --production-db /absolute/production.duckdb
python -m app.news_events_cli news-events-status --research-db /absolute/research.duckdb --production-db /absolute/production.duckdb
```

## Safe future probes (not authorized by this milestone)

1. SEC submissions: one known issuer, fixed request/byte/time budget, official
   endpoint and contact-bearing User-Agent; compare item and amendment metadata.
2. One issuer IR feed: written terms review first, HEAD/one response only, retain
   no body, verify timezone and correction semantics.
3. One government award/enforcement API: terms review, one known entity, verify
   durable entity-to-security mapping and publication semantics.
4. One contracted news sandbox: confirm historical entitlement, corrections,
   redistribution, retention/deletion, identifier fields, and request limits.

Every probe needs separate authorization. Yahoo/open-web probes remain excluded
until timestamp provenance and licensing are defensible.
