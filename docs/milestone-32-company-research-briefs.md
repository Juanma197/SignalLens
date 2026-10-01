# Milestone 32 — explainable company research briefs

## Safety contract

Briefs are deterministic, read-only descriptions of evidence known at an explicit, timezone-aware decision timestamp. They are labelled **PAPER RESEARCH ONLY — NOT INVESTMENT ADVICE.** They never download data, read filing bodies, create a vintage, publish a ranking, predict a price, or recommend a security. Stored event text is untrusted: output is HTML-escaped and limited to 240 characters; events are limited to ten and paper groups to three.

Both explicit database files are validated as distinct regular files and fingerprinted before and after every operation. Responses expose only the immutability result—not paths or hashes. Unknown/unqualified/ambiguous symbols, ticker reuse without effective-date resolution, future timestamps, non-model-ready prices, and unsafe aliases fail closed. Missing dilution remains viewable only with explicit score withholding and missing-data output.

Symbol failures have typed, stable public classifications; neither the CLI nor API inspects exception text. `COMPANY_BRIEF_INVALID_SYMBOL` is reserved for malformed syntax (including empty, non-normalized/lowercase, or missing exchange suffix input). A normalized symbol absent from the active catalogue returns `COMPANY_BRIEF_UNKNOWN_SYMBOL`. More than one active identity, including unresolved ticker reuse, returns `COMPANY_BRIEF_IDENTITY_AMBIGUITY`. A unique active-catalogue identity that is absent from the frozen scored cross-section returns `COMPANY_BRIEF_EVIDENCE_UNAVAILABLE`. All responses redact database paths, SQL, tracebacks, provider payloads, and internal exception details.

Three distinct readiness sets must not be conflated:

1. **Catalogue-valid** means the normalized symbol resolves uniquely in the active catalogue snapshot. It establishes identity, not score eligibility.
2. **Generally model-ready** means the security passes the broader observation-readiness policy at the decision boundary. General readiness totals can therefore exceed a particular frozen cross-section.
3. **Frozen-score-ready** means the security actually has the evidence required by the registered score reconstruction and appears in that exact frozen scored cross-section.

A security can be catalogue-valid and have visible price rows, or even be generally model-ready, without being frozen-score-ready. In that case the brief is withheld as `COMPANY_BRIEF_EVIDENCE_UNAVAILABLE`; the adapter never fills the gap with a current, neutral, inferred, or otherwise manufactured score.

## Pre-vintage repair (October 2026)

The initially merged adapter incorrectly sent an individual brief's calendar date to the prospective-vintage database input path. That path intentionally accepts only a completed US month-end session, so the valid `2026-10-01T12:00:00Z` operator request failed before a brief could be assembled. The correction separates brief evidence lookup from vintage planning; the prospective plan/create path and its completed-month-end gates remain unchanged.

After the registered strategy timestamp, an individual pre-vintage brief may be requested at any timezone-aware, non-future timestamp. It uses the active identity and the latest model-ready price observation, dilution facts, contextual fundamentals, and SEC event metadata that were public **and retrieved** no later than that timestamp. It never advances the cutoff to a later month-end. When required dilution score evidence is absent, the brief explicitly withholds the dilution percentile, contribution, and combined score rather than assigning a neutral or invented value.

Until a registered prospective vintage exists, `why_it_is_being_viewed` says only that no paper selection exists; it does not call the company selected, ranked, recommended, or a Top 3 member. Once a vintage exists, selection status and displayed 90/10 contributions come from the latest stored vintage at the requested boundary. An unselected company is reported as unselected for that stored vintage, and no current-evidence score is substituted for the frozen record.

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
