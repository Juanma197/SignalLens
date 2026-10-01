# Milestone 32 — explainable company research briefs

## Safety contract

Briefs are deterministic, read-only descriptions of evidence known at an explicit, timezone-aware decision timestamp. They are labelled **PAPER RESEARCH ONLY — NOT INVESTMENT ADVICE.** They never download data, read filing bodies, create a vintage, publish a ranking, predict a price, or recommend a security. Stored event text is untrusted: output is HTML-escaped and limited to 240 characters; events are limited to ten and paper groups to three.

Both explicit database files are validated as distinct regular files and fingerprinted before and after every operation. Responses expose only the immutability result—not paths or hashes. Unknown/unqualified/ambiguous symbols, ticker reuse without effective-date resolution, future timestamps, non-model-ready prices, unavailable dilution, and unsafe aliases fail closed.

## Brief structure and permitted language

The stable sections are company identity; why it is being viewed; price behaviour; dilution/share-count evidence; financial context; recent official filings/events; risks and warnings; missing information; model status; and source citations. Price evidence describes momentum, trend, volatility, drawdown, history quality, and a strong/moderate/weak eligible-US-peer percentile. Dilution reports the exact year-over-year change, ownership effect, public date, accession provenance, and its exact 10% contribution.

Fundamental rows are labelled `supportive`, `cautionary`, `mixed`, or `unavailable`. They are context only and have zero score effect. The failed 40% fundamentals composite is never presented as valid. Event descriptions are factual, use accession citations, and are never labelled bullish or bearish. Missing SEC item codes produce “detailed classification unavailable.” Flags are descriptive and are not automatic instructions.

## Frozen score and vintages

The only registered prospective formula remains 90% price percentile plus 10% dilution percentile. A registered selection explains the final score, both contributions, percentile when stored, deterministic qualified-symbol tie-break, and entry into the bounded zero-to-three paper group. No selection is synthesized. Before the first permissible October 2026 month-end, the adapter explicitly reports that no paper selection exists yet and uses no live issuer as an example.

## Post-merge read-only PowerShell examples

Run from `backend` after substituting two explicit, distinct local database paths and a real exchange-qualified catalogue symbol:

```powershell
python -m app.company_research_cli company-research-brief `
  --research-db 'C:\SignalLens\data\research\signallens-research.duckdb' `
  --production-db 'C:\SignalLens\data\signallens.duckdb' `
  --qualified-symbol 'SYMBOL.US' `
  --decision-at '2026-10-31T23:59:59+00:00'

python -m app.company_research_cli prospective-selection-briefs `
  --research-db 'C:\SignalLens\data\research\signallens-research.duckdb' `
  --production-db 'C:\SignalLens\data\signallens.duckdb' `
  --decision-at '2026-10-31T23:59:59+00:00'
```

These commands are examples only; do not execute them against operator data as part of the milestone.

## Railway and dashboard integration

Authenticated `GET /api/v1/research/company-brief` and `GET /api/v1/research/prospective-selection-briefs` use Railway’s existing database settings and bearer authentication. The Next.js `/research` page calls the server-side authenticated proxy, displays purpose-built score cards, a financial-context table, an SEC timeline, flags/citations and known-at timestamp, and does not expose credentials or filesystem details. Deployment, scheduling, and publication remain separate operator actions and are not part of this milestone.
