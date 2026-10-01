# Milestone 23 — point-in-time company-intelligence capability assessment

## Safety boundary and baseline

This is a bounded **US capability assessment**, not a model change. It does not
tune a threshold, select a horizon, create a vintage, publish a ranking, or
generate candidates. The frozen 21/63/126/252-session evaluation remains the
immutable baseline: all four final decisions failed. The 126-session result was
promising but failed regional stability and family-wise multiple-testing
adjustment; 252 sessions was positive in all five regions but failed confidence
and the same adjustment. **No candidates generated.**

The implementation is storage-free and defaults closed. Offline fixtures require
an explicit fixture-mode argument. A future adapter must receive an explicit
`live_authorized=True` before either a network request or persistence. This
milestone makes neither, calls neither EODHD nor Railway, and modifies no
database. Fiscal-period end is descriptive metadata and is never availability.

## Provider-neutral observation contract

Every observation carries a stable security and observation ID, feature family,
metric, typed value, unit, ISO currency where applicable, fiscal/event period,
`public_at`, `retrieved_at`, source identity/type/URL/terms/document ID, filing
type, amendment sequence and supersession link, plus optional expiry. Numeric,
event, transaction and news observations share this contract. Amendments become
visible only at their own public timestamp and never overwrite earlier history.

The intended derived families are revenue and earnings growth; operating/net
margins and return on invested capital; free cash flow; debt and interest
coverage; diluted shares and dilution; valuation ratios; filing and earnings
dates; legally available insider/government transactions; and news/catalyst
metadata. Derived values must inherit the latest `public_at` of every input.

## Sanitized US capability matrix

| Feature family | Status | Bounded source assessment |
|---|---|---|
| Filing dates; filed XBRL revenue, earnings, balance-sheet, cash-flow and share facts | **Point-in-time usable** | Official SEC EDGAR submissions and Company Facts; accession and SEC acceptance/filing timestamp retained. Calculation still requires issuer-tag mapping and unit validation. |
| Growth, margins, return on capital, free cash flow, debt/interest coverage and dilution | **Point-in-time usable when inputs pass validation** | Derive only from SEC facts known by the decision timestamp; missing or incompatible concepts fail closed. Do not backfill a later amendment. |
| Issuer earnings releases and investor-relations catalysts | **Point-in-time usable per document** | Official issuer disclosure with the release timestamp and canonical URL. Historical completeness varies and must be measured issuer by issuer. |
| SEC Forms 3/4/5 insider transactions | **Point-in-time usable** | Official SEC ownership filings where legally public; transaction date is not availability—SEC acceptance is. |
| US federal elected-official transactions | **Latest-only / delayed, not trading-time evidence** | Official House/Senate disclosures may be legally public but report windows and entity matching make them unsuitable for precise event timing without a separately validated archive. |
| Valuation | **Latest-only until point-in-time price joins are proven** | SEC denominators can be point-in-time; market price/share alignment and corporate actions need a separately frozen historical join. Current fundamentals alone are insufficient. |
| Government contracting and lobbying transactions | **Unavailable in this milestone** | No normalized, identity-resolved, point-in-time official feed has been validated. |
| Google News RSS discovery | **Latest-only** | Useful for discovery, not a guaranteed archive or licensed full-text corpus; retain publisher URL, timestamps and retrieval provenance only. |
| GDELT metadata | **Latest-only pending coverage validation** | Low-cost event metadata can support discovery, but issuer matching, corrections, archival stability and terms need validation before model use. |
| SEC/issuer releases as news catalysts | **Point-in-time usable** | Official public records are preferred; store metadata and provenance, not republished article text. |
| Yahoo Finance undocumented endpoints/scraping | **Legally or technically unsuitable** | Explicitly excluded because interface stability, authorization and reuse rights are not sufficiently documented for this capability. |
| Subscription-restricted EODHD fundamentals | **Unavailable** | HTTP 403 established lack of entitlement; no call is made and no workaround is permitted. |

## Acceptance evidence

Deterministic tests cover filing boundaries, amendments/restatements, missing
availability, duplicates, units/currencies, stale news, complete provenance,
future leakage, explicit offline/live modes and byte-level database immutability.
Before any future live pilot, legal/terms review, SEC fair-access identification,
rate limits, issuer coverage sampling and a separate authorized storage design
are mandatory. None of those future steps authorizes candidate generation.
