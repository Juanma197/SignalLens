# Milestone 38 — authorized investment-evidence materialization

Milestone 37 correctly reported zero classified securities because its point-in-time classification and canonical-factor stores were intentionally empty. Milestone 38 adds a research-only evidence workflow; it does **not** add a Track B model or Top 3.

## Safety and time semantics

Back up research before writing. Commands require explicit, distinct regular database files; symlinks, aliases, and hard links are rejected. Production is opened read-only and fingerprinted before and after. Writes use a research transaction and preserve source rows. Every normalized record uses `available_at = max(public_at, retrieved_at)`. `materialized_at` records this workflow only and never moves evidence backward in time.

Classification fails closed and refuses conflicts. The hierarchy is authoritative SEC entity metadata; SEC submissions metadata; filing forms establishing a reporting regime; authoritative catalogue instrument type; effective-dated listing metadata; and explicit reviewed evidence. A ticker suffix, name, one concept, or zero revenue is never sufficient. Foreign issuers/ADRs and SPAC common/unit securities remain distinct.

Canonical evidence references the original `sec_facts` key and accession. Audited aliases require compatible semantics and units. Revisions become visible only after publication; the latest visible revision is selected for its exact period. Instant, quarterly, YTD, and annual intervals stay distinct, and overlapping intervals are not summed. Capex has an explicit positive-outflow convention. Missing debt is unknown, never zero; debt-free status requires both components explicitly zero.

Corporate-action coverage is `verified_no_action`, `action_present`, `unresolved_action`, or `coverage_missing`. Only a completed assessment checkpoint can prove no action; absence of an event cannot. The assessed interval, source, and retrieval time are retained.

## Exact post-merge PowerShell commands

```powershell
$Research = "C:\SignalLens\data\research.duckdb"
$Production = "C:\SignalLens\data\production.duckdb"
$Decision = "2026-10-01T00:00:00+00:00"
Copy-Item $Research "$Research.m38-backup-$(Get-Date -Format yyyyMMddHHmmss)"
python -m app.investment_research_cli plan-investment-evidence-materialization --research-db $Research --production-db $Production --decision-at $Decision
python -m app.investment_research_cli materialize-stored-investment-evidence --research-db $Research --production-db $Production --decision-at $Decision --authorization "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"
python -m app.investment_research_cli investment-evidence-materialization-status --research-db $Research --production-db $Production --decision-at $Decision
python -m app.investment_research_cli plan-investment-evidence-enrichment --research-db $Research --production-db $Production --decision-at $Decision
# One issuer pilot:
python -m app.investment_research_cli enrich-investment-evidence-from-sec --research-db $Research --production-db $Production --decision-at $Decision --authorization "I AUTHORIZE RESEARCH-ONLY SEC INVESTMENT EVIDENCE ENRICHMENT" --user-agent "SignalLens research ops@example.com" --request-budget 2 --max-issuers 1
# Resume (completed issuers excluded):
python -m app.investment_research_cli enrich-investment-evidence-from-sec --research-db $Research --production-db $Production --decision-at $Decision --authorization "I AUTHORIZE RESEARCH-ONLY SEC INVESTMENT EVIDENCE ENRICHMENT" --user-agent "SignalLens research ops@example.com" --request-budget 20
# Retry retryable checkpoints:
python -m app.investment_research_cli enrich-investment-evidence-from-sec --research-db $Research --production-db $Production --decision-at $Decision --authorization "I AUTHORIZE RESEARCH-ONLY SEC INVESTMENT EVIDENCE ENRICHMENT" --user-agent "SignalLens research ops@example.com" --request-budget 20
python -m app.investment_research_cli comparable-universe-research-readiness --research-db $Research --production-db $Production --decision-at $Decision
python -m app.investment_research_cli company-investment-factor-preview --research-db $Research --production-db $Production --decision-at $Decision --qualified-symbol "NEU.US"
```

The enrichment planner recalculates submissions, company-facts, mapping-review, and instrument-review needs. Live enrichment is separately authorized and bounded by request/runtime/attempt/pacing/timeout/body limits. Only SEC submissions and company-facts metadata are permitted; no response body or filing text is stored. Mapping candidates are review-only and mapping approval is separate.

Authenticated reads: `/api/v1/research/investment-evidence-materialization-status`, `/api/v1/research/investment-evidence-enrichment-plan`, `/api/v1/research/comparable-universe-readiness`, and `/api/v1/research/company-factor-preview`.

Track A remains frozen. Track B emits no score, percentile, rank, candidate, selection, vintage, validation observation, recommendation, allocation, or Top 3.
