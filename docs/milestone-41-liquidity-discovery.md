# Milestone 41: liquidity evidence discovery

## Boundary and observed gap

This is **TRACK B RESEARCH FOUNDATION — NOT A MODEL**. It generates no purchase
recommendation, candidate, ranking, selection, vintage, or validation credit. At
the `2026-10-02T18:15:00+00:00` operator boundary, the comparable population was
71 US operating companies and existing liquidity readiness was 0/71. Milestone
41 explains that gap without ingesting, repairing, normalizing, or mutating data.
It does not activate an alias or select a Track B contract.

The audit reads both databases in DuckDB read-only mode and fingerprints each
before and after. It never contacts SEC, a provider, Railway, or another service.
The raw stored SEC facts are the discovery source; canonical evidence is a
supplement when the same raw observation is absent. Exact aggregate counts use
the entire comparable population, while returned detail is bounded.

## Exact discovery and accounting rules

The versioned discovery rule uses a case-sensitive allow-list for the core
concepts and reports other exact names only when they contain one of the published
liquidity tokens. A token or similar label is not an alias. Each occurrence keeps
taxonomy/version when stored, balance type (or `unknown_not_stored`), instant or
duration nature, unit, currency, scale, sign, period end, form, accession, public,
retrieval, and canonical-availability timestamps. Reports give observation and
company coverage, latest visible periods, relationship (direct, broader,
narrower, or incompatible), confidence, and the exact acceptance/withholding
reason.

Accepted balance-sheet facts must be instant, finite and nonnegative; use USD or
monetary units with USD currency; and have a finite positive scale. Public and
retrieval timestamps must exist and be no later than the decision. When canonical
availability is stored, it must equal their maximum. Facts older than 550 days
are stale. Company, date, currency, scale, and accounting scope must match for a
construction. Conflicting values for the same semantic period are disclosed and
withheld from a clean diagnosis.

The high-confidence direct proposal covers `AssetsCurrent`,
`LiabilitiesCurrent`, `CashAndCashEquivalentsAtCarryingValue`, and `Assets`.
`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` is a broader cash
aggregate, while `RestrictedCashAndCashEquivalentsCurrent`, `ShortTermInvestments`,
`MarketableSecuritiesCurrent`, `InventoryNet`, `AccountsReceivableNetCurrent`, and
`AccountsPayableCurrent` are separately assessed components. Total liabilities
are not current liabilities. Every output proposal is versioned
`liquidity-alias-proposal-1.0.0` and has status `proposed_not_authorized`.

## Cash separation and construction risks

Unrestricted cash, restricted cash, combined cash plus restricted cash,
short-term investments, and marketable securities are never silently equated.
Unrestricted cash is preferred directly. Combined cash may be decomposed only
when separately reported restricted cash matches company, period, USD currency,
scale, and decision boundary; a negative result is refused. Investments and
marketable securities are not silently added to cash.

Working capital is current assets minus current liabilities. It is valid when
both instant observations match even if the result is negative; zero current
liabilities does not prevent reporting working capital. Ratios require a strictly
positive denominator. Current ratio uses direct current assets and liabilities.
Quick assets may be assessed as current assets minus inventory only with fully
compatible facts—never merely because both labels exist. Cash ratio uses direct
unrestricted cash or a disclosed compatible decomposition. Working-capital-to-
assets additionally requires compatible, positive total assets. Net-debt-to-assets
requires an independently defensible debt construction and unrestricted cash;
this milestone does not relax Milestone 40's debt rules. Financial companies need
a separate accounting contract.

Counterfactuals show existing readiness, high-confidence direct-alias readiness,
compatible-construction readiness, direct cash coverage, direct-or-constructed
cash coverage, and effects on Contracts A–D. They keep `contract_selected: null`.
Coverage does not authorize whichever choice produces the largest sample.

## Unresolved judgments

Review remains necessary for issuer extensions, balance attributes not retained
in storage, current/non-current classification differences, cash legally or
contractually unavailable despite an unrestricted-looking tag, the scope of
marketable securities, receivable and inventory definitions, consolidated versus
parent scope, fiscal-calendar staleness, finance-sector presentation, debt/lease
scope, and whether a broader aggregate can ever be decomposed safely. No judgment
is resolved from a similar label or from coverage alone.

An eventual materialization requires a separate authorization: approve a new
alias-contract version, pin exact concepts and accounting exclusions, add an
idempotent plan/apply workflow with database backup and immutable audit journal,
rerun point-in-time regression tests, and only then update consumers. This
milestone intentionally provides no apply command and leaves financial-strength
readiness unchanged.

## Output guarantees

Aggregate discovery is at most 512 KiB compact UTF-8. Contract assessment and one
company preview are each at most 256 KiB. Samples contain at most 10 companies,
symbols, concepts, and citations; preview alternatives contain at most three
observations per canonical field. Collections publish total/returned counts and
truncation. Errors are stable and redact paths, SQL, and payloads. Aggregate APIs
never return unrestricted observations, filing bodies, provider payloads,
filesystem paths, SQL, or an unbounded company list.

## Exact post-merge PowerShell procedure

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$BeforeResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$BeforeProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.investment_research_cli liquidity-evidence-discovery `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-evidence-discovery.json"
python -m app.investment_research_cli liquidity-contract-assessment `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-contract-assessment.json"
python -m app.investment_research_cli liquidity-company-preview `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --qualified-symbol "EXAMPLE.US" |
  Set-Content -Encoding utf8 "..\liquidity-company-preview.json"
Pop-Location

$AfterResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$AfterProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
  throw "Read-only invariant failed"
}
python -c "import json,pathlib; fs=['liquidity-evidence-discovery.json','liquidity-contract-assessment.json','liquidity-company-preview.json']; [print(f,len(json.dumps(json.loads(pathlib.Path(f).read_text(encoding='utf-8-sig')),sort_keys=True,separators=(',',':')).encode('utf-8'))) for f in fs]"
```

Verify `read_only`, both immutability flags, proposal status, null contract
selection, all empty zero-output arrays, and zero validation credit. Do not run an
ingestion, repair, enrichment, or materialization command.

Track A remains `prospective-us-dilution-1.0.0`, configuration hash
`7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`, registered
`2026-10-01T00:00:00Z`, with its 90% price / 10% dilution percentiles, maximum
three selections, exact 126/252-session outcomes, comparators, and tie-breaking
unchanged.

## Post-Milestone-41 raw-versus-canonical reconciliation

The operator run at `2026-10-02T18:15:00+00:00` found 71 comparable companies,
0/71 ready for each required field, 71/71 `only_broader_aggregate_exists` for
current assets (the stored concept was `Assets`), and 71/71
`no_relevant_fact_stored` for current liabilities and unrestricted cash. All
direct and constructed metrics were consequently 0/71. Proposals remained
unapproved and both fingerprints were unchanged.

Code review established that Milestone 41 did read `sec_facts` from **both** the
research and production databases, then supplemented those raw observations with
canonical rows only when their stable observation identity was not already in
the raw set. It therefore did not query canonical materialization alone. Its
diagnosis, however, merged raw and canonical evidence and could not prove which
layer caused an absence. The two new commands preserve the point-in-time reader
boundary while reporting that distinction explicitly.

### Persisted SEC/XBRL evidence path

| Database/table | Layer | Identity and join | Concepts | Measurement/period | Availability |
|---|---|---|---|---|---|
| research and production `sec_facts` | raw SEC companyfacts observation | `security_id`, `qualified_symbol`, `cik`, `fact_key`; exact `security_id` joins the comparable classification, with CIK checked through `sec_issuers` | `taxonomy`, `concept` | `value`, `unit`, `currency`, `period_start`, `period_end`, fiscal period/frame; source does not retain an explicit scale | `filed_date`, `public_at`, `retrieved_at` |
| research `canonical_factor_evidence` | canonical/materialized | `security_id`, `qualified_symbol`, `evidence_key`, `source_fact_key`; exact `security_id` join | `canonical_field`, `original_concept_or_field`, alias contract | `value`, `unit`, `currency`, start/end/instant and fiscal period; source scale may be in provenance | public, retrieved, canonical available, and materialized timestamps |
| research and production `sec_issuers` | normalized identity bridge | `security_id` and SEC `cik`; no ticker or name inference is permitted | not applicable | not applicable | `mapped_at` |
| research and production `sec_filings` | normalized filing metadata | `cik` and `accession_number`, joined through the issuer bridge | not applicable | form and filed date | `public_at`, `retrieved_at` |

The commands also emit this catalog from the actual schemas, omitting tables
that do not exist. `security_classification_evidence` defines the comparable
universe; it is classification evidence rather than an SEC fact source. No row
is associated by ticker or company name.

### Interpretation of the reconciliation

The inventory assigns exactly one state to every company/required-field pair.
Canonical compatible evidence wins. Compatible raw evidence can establish
feasibility but is reported as `compatible_raw_fact_not_materialized`, never
promoted. Post-decision, stale, duration, and incompatible-unit facts remain
withheld. Extensions are separately inventoried as review-required and never
activated. `Assets` is only a broader aggregate, and combined cash is not
unrestricted cash. Same-period conflicts fail closed.

The operator databases, rather than this repository, contain the final exact
counts. In the reported 0/71 run, Milestone 41's raw concept discovery showing
only `Assets`, `NetCashProvidedByUsedInOperatingActivities`, and
`free_cash_flow` is evidence that its raw reader ran; the new inventory is the
required definitive check for research/production copies, identity conflicts,
post-decision observations, and canonical omissions. Interpret outcomes as:

| Inventory outcome | Exact remaining gap |
|---|---|
| compatible raw, no canonical row | materialization gap: the exact standard raw fact was not represented by usable canonical evidence |
| no relevant raw or canonical fact | ingestion gap: the exact required standard concept is absent from stored SEC facts at the boundary |
| extension or conflicting visible facts | accounting review; do not activate an alias |
| unresolved issuer identity | identity repair using durable `security_id`/CIK evidence, never ticker/name inference |

### Safe post-merge PowerShell verification

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$BeforeResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$BeforeProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.investment_research_cli liquidity-raw-canonical-inventory `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-raw-canonical-inventory.json"
python -m app.investment_research_cli liquidity-evidence-gap-assessment `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-evidence-gap-assessment.json"
Pop-Location

$AfterResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$AfterProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
  throw "Read-only invariant failed"
}
```

Verify all 213 company/field classifications sum exactly, inspect the bounded
samples and before/after fingerprints, and confirm every ranking, candidate,
recommendation, selection, vintage, and validation collection is empty with
zero validation credit. These commands make no network request, perform no
ingestion or materialization, activate no alias, and write no database.

## Milestone 43: corrected aggregation and safe ingestion planning

The `2026-10-02T18:15:00+00:00` operator reconciliation examined 213 cells for
71 companies. All 71 current-assets cells had only the broader `Assets`
aggregate (5,796 raw observations and zero `AssetsCurrent` observations). All
71 current-liabilities cells and all 71 unrestricted-cash cells had no relevant
raw or canonical fact. There were zero observations for `LiabilitiesCurrent`
and for every contracted direct/combined/restricted-cash/investment concept,
zero extensions, zero identity failures, and zero compatible facts either
materialized or omitted. Both databases were byte-for-byte unchanged.

The former company aggregation incorrectly required **all three** fields to be
`no_relevant_raw_or_canonical_fact`. Because every company had broader `Assets`
for one field, it returned zero ingestion companies despite 142 absent cells.
The corrected flags use `any`: one absent required field means ingestion, while
broader-only, incompatible, extension, or conflicting evidence independently
means accounting review. Thus the operator result reconciles as follows (these
are derived classifications, not hard-coded production counts):

| Independent company issue | Correct count | Reason |
|---|---:|---|
| requires new SEC ingestion | 71 | every company lacks current liabilities and unrestricted cash |
| requires accounting review | 71 | every company has only broader `Assets` for current assets |
| requires canonical materialization | 0 | no compatible exact raw fact was omitted |
| identity failure | 0 | no issuer identity was classified unresolved |

### Current ingestion-contract diagnosis

The cause is selection before persistence, not evidence that SEC itself lacks
the facts. `sec_capability.CONCEPTS` is the current allowlist consumed by
`normalize_facts`; it includes `Assets` under asset return but none of
`AssetsCurrent`, `LiabilitiesCurrent`, or the liquidity cash/restriction and
working-capital concepts. `normalize_facts` walks only that allowlist, and the
ingester persists those normalized observations to `sec_facts`. It does not
retain the complete Company Facts response. This exactly explains why `Assets`
can be present while requested liquidity concepts are absent.

Existing ingestion uses a ticker-map request followed by SEC submissions and
Company Facts requests, a 205-request ceiling, at most two attempts by default,
20-second timeouts, and 0.12-second minimum pacing with retry backoff. Durable
`sec_checkpoints` record issuer state, attempts, and last run; retry mode selects
failed/incomplete work. `sec_ingestion_runs` records budgets and counts, while
`sec_failures` records stable failure codes. `sec_filings` supplies filed/public
availability and `sec_facts` supplies retrieval provenance. Historical facts
must therefore be requested again for issuers without a recognized retained
payload; missing rows alone never establish provider absence.

The planner checks the exact comparable population and CIK bridge rather than
assuming 71 mappings. With 71 mapped issuers and no retained payloads, the
projection is **142 requests**: one submissions request plus one Company Facts
request per issuer. Mapping retrieval is deliberately outside that estimate
because an unmapped issuer blocks the plan rather than permitting ticker/name
inference. The ceiling defaults to 205. Complete original payload replay is
reported available only when an explicitly recognized payload table contains a
non-empty artifact with an exact CIK; transformed `sec_facts` rows are not
treated as replayable payloads. The current persistence contract defines no raw
payload table, so existing normalized rows alone cannot be replayed.

The concept contract remains exact and separate:
`AssetsCurrent`, `LiabilitiesCurrent`,
`CashAndCashEquivalentsAtCarryingValue`,
`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`,
`RestrictedCashAndCashEquivalentsCurrent`,
`RestrictedCashAndCashEquivalents`, `ShortTermInvestments`,
`MarketableSecuritiesCurrent`, `InventoryNet`,
`AccountsReceivableNetCurrent`, `AccountsPayableCurrent`, `Assets`, and
`Liabilities`. `Assets` never maps to `AssetsCurrent`; combined cash never
becomes unrestricted cash without compatible restricted-cash decomposition;
issuer extensions remain review-only.

### Read-only plan schema and commands

`plan-sec-liquidity-evidence-ingestion` returns the exact population, mapped and
unmapped counts, requested concepts, ingestion/replay/live cohorts, deterministic
two-per-live-issuer request estimate, ceiling, checkpoint strategy, destination
tables, subsequent materialization step, database fingerprints, deterministic
 plan identifier, 15-minute expiry, status, and stable blocker codes. Samples
are capped at ten and the complete compact JSON is bounded. It opens databases
read-only, makes zero HTTP requests, and performs zero writes.

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$BeforeResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$BeforeProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.sec_ingestion_cli plan-sec-liquidity-evidence-ingestion `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --max-request-budget 205 |
  Set-Content -Encoding utf8 "..\sec-liquidity-ingestion-plan.json"
Pop-Location

$AfterResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$AfterProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
  throw "Read-only invariant failed"
}
```

### Controlled plan, apply, and status workflow (Milestones 41–43)

The canonical budget option is **`--max-request-budget`**. There is no
`--max-requests` alias for these liquidity commands. Generate a **fresh plan
immediately before apply**: plan identifiers are database-, decision-, budget-,
population-, and generation-time-bound and expire after 15 minutes. Never reuse
the earlier displayed operator plan. The expected current workload is 71 issuers
and 142 attempted requests (submissions plus Company Facts); retries also count
against the 205-request ceiling.

Back up both databases first and retain their SHA-256 values. Apply opens
production read-only, mutates only research, operates sequentially with a
descriptive contact-bearing `SIGNALLENS_SEC_USER_AGENT`, refuses redirects and
non-`data.sec.gov` hosts, paces requests, and applies bounded exponential retry
with jitter only to transient network/HTTP failures. It bounds connect/read
timeouts, response bytes, content type, JSON shape, and exact response CIK.

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$Authorization = "I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION"
$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research operations ops@example.com"
Copy-Item -LiteralPath $Research -Destination "$Research.pre-sec-liquidity.bak"
Copy-Item -LiteralPath $Production -Destination "$Production.pre-sec-liquidity.bak"
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
$PlanJson = python -m app.sec_ingestion_cli plan-sec-liquidity-evidence-ingestion `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --max-request-budget 205
$Plan = $PlanJson | ConvertFrom-Json
if ($Plan.status -ne "ready") { throw "SEC liquidity plan is not ready" }

python -m app.sec_ingestion_cli apply-sec-liquidity-evidence-ingestion `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --plan-identifier $Plan.plan_identifier --max-request-budget 205 `
  --authorization $Authorization

python -m app.sec_ingestion_cli sec-liquidity-evidence-ingestion-status `
  --research-db $Research --production-db $Production --decision-at $Decision
Pop-Location

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) {
  throw "Production immutability invariant failed"
}
```

Every completed issuer is committed atomically with two bounded raw-provenance
artifacts and a durable checkpoint. A partial/exhausted run keeps completed
issuers; generate another fresh plan and apply it to continue only incomplete
issuers. Evidence keys make repeated responses and facts idempotent. Unknown and
issuer-extension concepts remain only in the retained raw JSON. A filing date is
not treated as an exact public timestamp; observations without a defensible SEC
acceptance timestamp are withheld from normalized point-in-time use.

An active operation lock fails closed. If status calls it stale, first confirm
that the recorded process is gone, preserve a database backup, inspect the run
and checkpoint counts, then explicitly run (using the exact stale run ID):

```powershell
python -m app.sec_ingestion_cli recover-stale-sec-liquidity-ingestion-lock `
  --research-db $Research --production-db $Production --run-id "<stale-run-id>" `
  --authorization "I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION"
```

Recovery marks a still-running row partial and removes only that verified stale
lock; it never deletes evidence or checkpoints. To roll back operator ingestion,
stop all writers, preserve the failed database for audit, restore the research
backup, verify both saved hashes, and leave production untouched.

After completion or a partial run, use status with `--decision-at`, then run the
four read-only reconciliation commands: `liquidity-raw-canonical-inventory`,
`liquidity-evidence-gap-assessment`, `liquidity-evidence-discovery`, and
`liquidity-contract-assessment`. Review compatible facts awaiting canonical
materialization, genuinely absent concepts, stale/incompatible/post-decision
facts, issuer extensions, and remaining failures separately. Ingestion never
activates aliases and never materializes canonical evidence automatically.

Track A remains `prospective-us-dilution-1.0.0` with configuration hash
`7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`.
This work changes no Track A configuration, activates no alias, loosens no
accounting validation, and creates no Track B score, Top 3, ranking, candidate,
recommendation, selection, vintage, or validation credit.

## Milestones 41–43 controlled-ingestion isolation repair

### Operator observation and root cause

The pre-apply status returned 89 completed issuers, 11 permanent failures and 193
requests from a completed run whose plan identifier was null. No controlled liquidity
apply had run. Those values came from the older 100-security general SEC workflow.
The first controlled implementation reused and queried the general
`sec_ingestion_runs`, `sec_checkpoints`, and `sec_failures` tables without an
operation/contract predicate. It could therefore fail open by skipping issuers and
charging an unrelated request history to the controlled operation.

The repair uses immutable identity on every controlled run, checkpoint, failure,
lock, retained response and fact/filing association:

- `operation_type = sec_liquidity_evidence_ingestion`
- `operation_contract_version = 1.0.0`
- `concept_contract_hash` is the SHA-256 of the sorted exact concept list, endpoint
  classes, parser version, and validation rules.

Every controlled read requires all three exact values. Run/checkpoint resume also
requires the decision-boundary lineage, security ID, CIK, successful transaction,
matching run and plan lineage, and two validated retained endpoint payloads. A null
or conflicting identity or plan never qualifies. Diagnostics are bounded and must
not expose contact details.

### Legacy rows, migration, retries, and locks

Legacy rows are preserved exactly and are never relabelled. The controlled operation
uses operation-specific run, checkpoint, failure, and provenance tables. Schema
initialization is transactional and repeatable. Status opens both databases read-only
and performs no migration; against a pre-migration database it reports `never_run`
(or `planned` when a decision boundary permits cohort calculation), zero activity,
and no adopted legacy failures.

The request ceiling is **per apply attempt**. A fresh, unexpired plan may resume a
compatible partial lineage and skip only fully validated controlled checkpoints; it
does not inherit the prior attempt's consumed request count. Status reports the
latest attempt's count and remaining attempt budget.

A controlled liquidity lock is recognized only with the exact identity. An
unidentified, general-SEC, or other writer lock is reported separately and blocks an
unsafe apply, but controlled stale-lock recovery can delete only an exact-identity
liquidity lock selected by run ID. It never deletes another operation's lock.

Production fingerprints are recorded before and after each controlled attempt.
`unchanged` is only boolean when both values exist; no run is `not_applicable`, and
an incomplete attempt without an after value is `unavailable`. Null fingerprints
are never presented as evidence of change.

### SEC User-Agent requirement

Before transport construction, network access, schema initialization, or lock
creation, live apply requires `SIGNALLENS_SEC_USER_AGENT` to contain a descriptive
identity and plausible monitored email address. Blank values, the repository
placeholder, `YOUR_REAL_EMAIL_ADDRESS`, `example.com`, and placeholder text are
rejected with the stable redacted code `SEC_LIQUIDITY_USER_AGENT_INVALID`. Status and
failure output never include the address.

### Corrected never-run status (abridged)

```json
{
  "state": "planned",
  "latest_run": null,
  "completed_issuer_count": 0,
  "failed_issuer_count": 0,
  "remaining_issuer_count": 71,
  "actual_provider_request_count": 0,
  "raw_payload_provenance_count": 0,
  "checkpoint_states": {},
  "operation_lock": {"state": "none"},
  "production_unchanged_evidence": {
    "state": "not_applicable", "unchanged": null, "changed": null
  },
  "post_ingestion_reconciliation_ready": false
}
```

### Exact post-merge preflight

Discard **all plan identifiers printed before this repair was merged**. From the
repository root, run the following without invoking apply:

```powershell
$decisionAt = "<THE_APPROVED_DECISION_AT>"
$researchDb = "<RESEARCH_DATABASE_PATH>"
$productionDb = "<PRODUCTION_DATABASE_PATH>"

python -m app.sec_ingestion_cli sec-liquidity-evidence-ingestion-status `
  --research-db "$researchDb" --production-db "$productionDb" `
  --decision-at "$decisionAt"

python -m app.sec_ingestion_cli plan-sec-liquidity-evidence-ingestion `
  --research-db "$researchDb" --production-db "$productionDb" `
  --decision-at "$decisionAt" --max-request-budget 205
```

Proceed no further unless status shows no liquidity run and
`operation_lock.state = none`, while any `other_writer_lock` is also `none`. Only
then set a non-placeholder `SIGNALLENS_SEC_USER_AGENT` and generate a **fresh
15-minute plan** immediately before the separately authorized apply. Re-run status
and retain its output. This verification procedure does not authorize or execute
apply.

## 2026-10-04 apply-preflight repair (Milestones 41–43)

The first operator apply failed safely during preflight: it returned exit code 1,
made **zero provider requests**, created no schema, run, checkpoint, failure,
provenance, or lock rows, and changed neither research nor production. The supplied
plan was issued at 18:20 UTC and apply ran after the clock crossed 18:21 UTC.

The confirmed root cause was that apply parsed the issue epoch for its expiry check
but then rebuilt the expected plan with the apply-time clock. Because generation
time is digest-bound, crossing the minute boundary produced a different identifier.
The old tests generated and applied with the same implicit current clock bucket, so
they never exercised this transition. The regression now plans at 18:20:00 and
successfully applies at 18:21:10; boundary tests also cover 18:20:59 and exact
expiration.

All pre-repair identifiers must be discarded. The only accepted identifier is the
versioned capability `v1:<issued_epoch>:<base64url_bound_payload>:<sha256_digest>`.
The payload binds the decision, both database fingerprints, exact issuer/security/
CIK cohort, concepts, request estimate and budget, operation type, contract version
and contract hash. It is an integrity checksum, **not authentication**. Its encoded
issue instant is immutable; apply reconstructs at that instant, and independently
rejects current time at or after the original 15-minute expiry. Expiry is never
extended or refreshed.

Public failures are bounded to `SEC_LIQUIDITY_PLAN_INVALID`,
`SEC_LIQUIDITY_PLAN_EXPIRED`, `SEC_LIQUIDITY_PLAN_FUTURE_ISSUED`,
`SEC_LIQUIDITY_PLAN_FINGERPRINT_CHANGED`, `SEC_LIQUIDITY_PLAN_DECISION_MISMATCH`,
`SEC_LIQUIDITY_PLAN_CONTRACT_MISMATCH`,
`SEC_LIQUIDITY_REQUEST_BUDGET_INSUFFICIENT`,
`SEC_LIQUIDITY_AUTHORIZATION_INVALID`, `SEC_LIQUIDITY_USER_AGENT_INVALID`,
`SEC_LIQUIDITY_OPERATION_LOCKED`, and `SEC_LIQUIDITY_INTERNAL_ERROR`. CLI output
never exposes exception classes, paths, contact addresses, response bodies,
secrets, or tracebacks.

Use this exact post-merge sequence: **status → fresh plan → read-only validation →
apply**. Validation needs no SEC User-Agent and performs no writes or requests.

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$Authorization = "I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION"
Push-Location backend

python -m app.sec_ingestion_cli sec-liquidity-evidence-ingestion-status `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision"

$PlanJson = python -m app.sec_ingestion_cli plan-sec-liquidity-evidence-ingestion `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --max-request-budget 205
$Plan = $PlanJson | ConvertFrom-Json
if ($Plan.status -ne "ready") { throw "SEC liquidity plan is not ready" }

$ValidationJson = python -m app.sec_ingestion_cli validate-sec-liquidity-evidence-ingestion-plan `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier "$($Plan.plan_identifier)" --max-request-budget 205
$Validation = $ValidationJson | ConvertFrom-Json
if (-not $Validation.valid) { throw "SEC liquidity plan validation failed: $($Validation.reason_code)" }

$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research operations <MONITORED_EMAIL>"
python -m app.sec_ingestion_cli apply-sec-liquidity-evidence-ingestion `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier "$($Plan.plan_identifier)" --max-request-budget 205 `
  --authorization "$Authorization"
Pop-Location
```

This repair changes no Track A configuration or behavior and authorizes no model
output, score, candidate, ranking, recommendation, selection, vintage, or
validation credit.
This repair changes no Track A configuration or behavior and authorizes no model
output, score, candidate, ranking, recommendation, selection, vintage, or
validation credit.

## Post-ingestion reconciliation evidence

The operator subsequently ran all four read-only reconciliation commands. Each
exited zero, each error log was empty, and SHA-256 checks confirmed that both
databases remained byte-for-byte unchanged. The reports showed 191 raw field
observations withheld correctly, consisting of 61 post-decision current-assets
observations, 61 post-decision current-liabilities observations, and 69
post-decision unrestricted-cash observations. This is expected: evidence retrieved
on October 4 cannot be made available to the October 2 decision without introducing
look-ahead. Retrieval does not rewrite historical availability, even when the SEC
filing itself was public earlier.

The reports also found 12 exact concepts absent from retained raw storage: current
liabilities for ten companies and unrestricted cash for two. Ten companies require
accounting review, including broader aggregates that must not be silently treated as
current assets. No alias was activated, no canonical evidence was materialized, all
contract coverage remained zero, and Track A and all prohibited model outputs
remained unchanged.

A completed controlled retrieval is now reported separately from an unattempted
ingestion gap. `requiring_new_sec_ingestion` excludes a security only when its exact
isolated checkpoint links to its controlled run and to retained provenance for both
SEC endpoint classes. An absent concept after that proof is reported under
`completed_retrieval_concept_absent`; it does **not** recommend repeating the same
provider requests. Fresh plans likewise assign zero live requests to those proven
completed securities. Legacy SEC runs, checkpoints, failures, or payloads do not
qualify. Post-decision facts remain withheld for the October 2 boundary and may be
evaluated only under a separately approved later decision boundary.

## Milestones 41–43 post-retrieval measurement reconciliation

The controlled operation completed all **71 issuers** with exactly **142 successful
provider requests**, no failures, and no production-byte change. Its durable
checkpoints therefore classify all 71 as `previously_completed_retrieval`; live
retrieval and estimated requests remain zero. An exact concept still absent after
that completed retrieval is `completed_retrieval_concept_absent`, never a reason to
repeat the SEC requests.

At `2026-10-04T21:30:00+00:00`, inventory reported 187 compatible direct facts
awaiting materialization (61 current assets, 61 current liabilities and 65
unrestricted cash), covering 69 companies. Discovery instead called the same
persisted population incompatible. Its incompatible population included those 187
facts plus four stale cash facts (191 observations in the diagnostic population).
The confirmed root cause was not an accounting difference: controlled Company Facts
rows carried authoritative SEC unit `USD`, while the separate locally projected
`currency` column was absent on the affected persisted representation. Inventory
accepted `unit=USD` without consulting that redundant column; discovery required
both `unit=USD` and `currency=USD`.

`liquidity-measurement-validator-1.0.0` is now authoritative for inventory,
discovery, contract assessment, company preview, compatibility audit and
materialization planning. SEC Company Facts' unit key establishes denomination.
The validator losslessly normalizes a null, blank, or `USD` redundant currency to
canonical USD **only** when source unit is exactly `USD`. It preserves source value,
unit, currency and scale in provenance. Missing, zero and one scales are identity
representations; any other explicit scale is withheld because applying it would be
ambiguous in this persistence contract. Explicit currency contradictions, non-USD
units, nonfinite or negative values, duration facts, unsupported taxonomies or
concepts, unavailable timestamps and stale periods remain withheld.

Stable reason codes are: `accepted`, `concept_not_contractual`,
`taxonomy_not_supported`, `visibility_timestamp_missing`,
`not_visible_at_decision`, `measurement_nature_duration`, `source_unit_not_usd`,
`unit_currency_contradiction`, `currency_ambiguous`, `scale_not_lossless`,
`value_nonfinite_or_invalid`, `value_negative`, and `period_stale`. No issuer,
domicile, exchange, ticker, company-name or comparable-universe inference supplies
currency.

Expected operator reconciliation after this repair is:

| Canonical field | Inventory accepted | Discovery accepted direct | Remaining withheld |
|---|---:|---:|---|
| current assets | 61 | 61 | 10 broader `Assets` only |
| current liabilities | 61 | 61 | 10 broader `Liabilities` only |
| unrestricted cash | 65 | 65 | 4 stale, 1 broader combined cash only, 1 absent |
| **total** | **187** | **187** | accounting distinctions preserved |

The audit compares stable evidence identities, not only totals, and fails closed on
an inventory-only or discovery-only identity. Total `Assets` never satisfies
`AssetsCurrent`; total `Liabilities` never satisfies `LiabilitiesCurrent`; combined
cash requires compatible restricted-cash evidence; investments are not cash; issuer
extensions remain review-only; and duration facts never become instant facts. The
October 4 retrieval remains invisible at the October 2 boundary and is not backdated.
The four stale cash facts remain withheld.

`plan-liquidity-canonical-materialization` is read-only. It proposes only shared,
accepted validator results and returns exact observation/company/field counts,
stable evidence keys, source-provenance requirements, database fingerprints, a
content-derived identifier, expiry, blockers and bounded samples. It performs zero
writes and provider requests, activates zero aliases, and creates no ranking,
candidate, recommendation, selection, vintage, model output or validation credit.
The subsequent controlled operation adds apply without changing this validator or
activating aliases. A reconciliation mismatch blocks readiness.

## Milestones 42–44: controlled canonical materialization

The 2026-10-04 21:30 UTC operator audit completed with 187 shared observations
(61 current assets, 61 current liabilities and 65 unrestricted cash) for 69
companies, no blockers, no requests and unchanged databases. The remaining ten
broader/non-current aggregates, four stale cash facts, one non-USD cash fact, one
absent cash fact, historical stale observations and one negative current-assets
observation stay withheld.

`liquidity_canonical_materialization` contract `1.0.0` binds its SHA-256 contract
document, validator `liquidity-measurement-validator-1.0.0`, exact fields and concept
mappings, normalization/scale/timestamp rules, evidence-key algorithm and write
schema into every plan and revision. The `lcm1` token is canonical JSON plus its
SHA-256 digest. It binds issue/expiry and decision times, both file fingerprints,
operation and validator identities, the ordered evidence-key set and digest, exact
field/company/observation counts, and successful reconciliation. Its 24-hour
lifetime is measured from the encoded `issued_at`; validation never refreshes that
instant, avoiding the former apply-time-clock defect.

Apply requires this exact, case-sensitive phrase:

`I AUTHORIZE RESEARCH-ONLY CANONICAL LIQUIDITY MATERIALIZATION`

All preflight checks precede the writable research connection. Production remains
read-only and is fingerprinted before and after. Revisions, manifests and failures
are operation-specific; retrying the identical plan inserts zero revisions and
reports all as unchanged. A conflicting key refuses rather than overwrites. Schema,
lock, manifest and evidence writes share one DuckDB transaction, so any escaping
error removes schema and data changes. Neither backup creation nor deletion is
automatic.

Capacity is conservative: **required free bytes = 2 × research file bytes + 512
MiB**. For the approximately 1.45 GB operator database, first create and verify an
offline operator-managed backup, then ensure the plan reports
`capacity_sufficient=true`. This reserves room for the original file, DuckDB
transaction/WAL behavior, and safety margin.

### Exact post-merge PowerShell materialization workflow

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-04T21:30:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$Authorization = "I AUTHORIZE RESEARCH-ONLY CANONICAL LIQUIDITY MATERIALIZATION"
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

# Operator creates and verifies an offline backup here; SignalLens does not do so.
Push-Location backend
$Plan = python -m app.investment_research_cli plan-liquidity-canonical-materialization `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" | ConvertFrom-Json
python -m app.investment_research_cli validate-liquidity-canonical-materialization-plan `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier $Plan.plan_identifier
python -m app.investment_research_cli apply-liquidity-canonical-materialization `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier $Plan.plan_identifier --authorization $Authorization
python -m app.investment_research_cli liquidity-canonical-materialization-status `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision"

foreach ($Report in @("liquidity-measurement-compatibility-audit",`
 "liquidity-raw-canonical-inventory","liquidity-evidence-discovery",`
 "liquidity-contract-assessment","financial-strength-contract-assessment",`
 "track-b-panel-feasibility")) {
  python -m app.investment_research_cli $Report --research-db "$Research" `
    --production-db "$Production" --decision-at "$Decision"
}
Pop-Location
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) {
  throw "Production immutability failed"
}
```

After verifying the named run is not active and is older than 30 minutes, recover
only its exact canonical-liquidity lock (never a legacy or SEC-ingestion lock):

```powershell
python -m app.investment_research_cli recover-stale-liquidity-canonical-materialization-lock `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --run-id "<exact-status-run-id>" --authorization $Authorization
```

Re-plan after recovery if the token expired or either source changed. Do not reuse a
failed/mismatched plan. Verification should show compatible canonical facts and
greater ratio coverage only where inputs coexist; it must leave the listed rejected
facts withheld and grant zero ranking, selection, recommendation, vintage or
validation credit.

### Exact post-merge read-only commands

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-04T21:30:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.investment_research_cli liquidity-measurement-compatibility-audit `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" |
  Set-Content -Encoding utf8 "..\liquidity-measurement-compatibility-audit.json"
python -m app.investment_research_cli plan-liquidity-canonical-materialization `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" |
  Set-Content -Encoding utf8 "..\liquidity-canonical-materialization-plan.json"
Pop-Location

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne $ResearchBefore) {
  throw "Research read-only invariant failed"
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) {
  throw "Production read-only invariant failed"
}
```

These commands must be run only after merging this repair. They access the two
explicit local database paths read-only, make no network request, and do not contact
Railway or any provider.

## Milestone 44 operator failure and legacy-schema repair

The first controlled operator apply failed with exit code 1 and the deliberately
redacted `LIQUIDITY_MATERIALIZATION_INTERNAL_ERROR`. Its preceding plan and
validation were valid: 187 observations for 69 companies (61 current assets, 61
current liabilities, and 65 unrestricted cash), sufficient capacity, and exact
authorization. Transaction rollback worked: research and production remained
byte-for-byte unchanged, status remained `never-run`, `latest_run` remained null,
no lock remained, and no materialization table survived.

Read-only diagnosis found 52,320 rows in the existing
`canonical_factor_evidence` table. Its exact contract is:

```text
evidence_key VARCHAR NOT NULL PRIMARY KEY
security_id VARCHAR NOT NULL
qualified_symbol VARCHAR NULL
canonical_field VARCHAR NOT NULL
value DOUBLE NULL; unit VARCHAR NULL; currency VARCHAR NULL
period_start DATE NULL; period_end DATE NULL; instant_date DATE NULL
fiscal_period VARCHAR NULL; form VARCHAR NULL
accession_or_source_identifier VARCHAR NOT NULL
public_at TIMESTAMPTZ NOT NULL; retrieved_at TIMESTAMPTZ NOT NULL
available_at TIMESTAMPTZ NOT NULL; materialized_at TIMESTAMPTZ NOT NULL
original_concept_or_field VARCHAR NOT NULL
alias_contract_version VARCHAR NOT NULL; sign_convention VARCHAR NOT NULL
reliability_state VARCHAR NOT NULL; withholding_reason VARCHAR NULL
provenance JSON NOT NULL; source_fact_key VARCHAR NULL; lineage JSON NOT NULL
```

The exact defect was the assumption that `CREATE TABLE IF NOT EXISTS` would make
an existing table match a new shape. `_ensure_canonical` then added operation
columns, and the canonical `INSERT` supplied those new names but omitted required
legacy columns. The first row therefore reached an equivalent explicit insert of
`evidence_key, source_evidence_key, security_id, ... provenance,
validator_version, operation_type, operation_contract_version,
operation_contract_hash, materialization_run_id, materialized_at` and failed the
legacy NOT NULL contract, first at `alias_contract_version` (with
`sign_convention` and `lineage` also absent). This was an insert-contract error,
not a validator, authorization, capacity, or evidence-population error.

The repair never evolves or rewrites that table. A shared read-only preflight
checks all 25 names, types, nullability, and the primary key; unsupported required
extra columns fail closed with
`LIQUIDITY_MATERIALIZATION_SCHEMA_INCOMPATIBLE`. Apply uses an explicit legacy
column list. Run, plan, revision, validator, normalization, and source identity
live in append-only operation tables and in the existing `provenance`/`lineage`
JSON. Canonical `available_at` is no earlier than materialization time, while the
original timestamps remain in provenance, so an earlier decision boundary cannot
observe a later materialization. No migration is necessary.

### Safe post-merge PowerShell sequence

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-04T21:30:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$Backup = "C:\SignalLens Backups\research-before-liquidity.duckdb"
$Authorization = "I AUTHORIZE RESEARCH-ONLY CANONICAL LIQUIDITY MATERIALIZATION"

$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
Copy-Item -LiteralPath $Research -Destination $Backup
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Backup).Hash -ne $ResearchBefore) {
  throw "Research backup verification failed"
}

Push-Location backend
python -m app.investment_research_cli liquidity-canonical-materialization-status `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision"
$Plan = python -m app.investment_research_cli plan-liquidity-canonical-materialization `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" | ConvertFrom-Json
$Validation = python -m app.investment_research_cli validate-liquidity-canonical-materialization-plan `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier $Plan.plan_identifier | ConvertFrom-Json
if (-not $Validation.valid) { throw "Plan validation failed: $($Validation.reason_code)" }
python -m app.investment_research_cli apply-liquidity-canonical-materialization `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision" `
  --plan-identifier $Plan.plan_identifier --authorization $Authorization
python -m app.investment_research_cli liquidity-canonical-materialization-status `
  --research-db "$Research" --production-db "$Production" --decision-at "$Decision"
Pop-Location

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) {
  throw "Production immutability failed"
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Backup).Hash -ne $ResearchBefore) {
  throw "Backup changed"
}
```

The first apply is expected to report 187 inserted and zero unchanged; status must
show its completed run and no lock. An identical authorized retry of the same plan
is byte-for-byte mutation-free and reports zero inserted and 187
unchanged. Do not run the retry as part of the one-apply production sequence.

On any failure, first run status read-only and compare both database hashes. An
ordinary transactional failure needs no cleanup: verify no lock or running
manifest survived, retain the failed output, and create a fresh plan after the
cause is corrected. Never delete tables or rows manually. If byte verification
unexpectedly fails, stop all writers, preserve the suspect file for diagnosis,
verify the offline backup hash again, and only then restore it with an
operator-controlled atomic replacement. Stale-lock recovery is allowed only for
the exact run ID reported by status, after the documented 30-minute threshold;
then discard the old token and plan again.

## Milestones 41–44 post-materialization consumer repair

The controlled run `46ab3c8a-975a-45a5-b161-1d97ee4099e4` completed at about
2026-10-05T00:18:22Z.  It inserted 187 append-only revisions for 69 companies:
61 current-assets, 61 current-liabilities, and 65 unrestricted-cash observations.
There were no unchanged rows or conflicts, no provider requests, and no model or
validation output.  Production and the verified pre-apply backup remained byte
identical and the operation lock was released.  This completed run is correct and
must not be rolled back, rewritten, or repeated.

At 2026-10-04T21:30:00Z the compatibility audit passed and withheld all 187 later
revisions (`not_visible_at_decision` 61/61/65), with exact reconciliation and zero
validation credit.  At 2026-10-05T00:30:00Z, compatibility audit, discovery, and
contract assessment instead returned the redacted `INVESTMENT_RESEARCH_NOT_READY`.

The failed invariant was in the consumer merge, not in materialization.  The raw
SEC row is identified by `fact_key`, while its revision has a new canonical
`evidence_key`; generic identity deduplication consequently treated the pair as
independent.  In addition, generic raw validation requires `available_at` to equal
`max(public_at, retrieved_at)`, whereas the append-only revision correctly has
`available_at = max(source availability, materialized_at)`.  Thus the canonical
row was simultaneously duplicated and interpreted under the wrong availability
contract.  The mismatch reached consumer reconciliation/response invariants and
was redacted by the CLI.  It was not alias filtering, accounting relaxation, or a
change to source evidence.

All liquidity readers now use one fail-closed resolver.  It recognizes only the
exact operation type, contract version/hash, and validator version; parses JSON
objects independent of key order; verifies the canonical key, completed run,
revision record, immutable source identity and accounting attributes; and joins
one revision to one raw `fact_key`.  It selects only revisions whose canonical
availability is visible, ordered by materialization time, availability time, and
finally evidence key.  The selected revision replaces (never supplements) its
raw source for calculation, while bounded diagnostics retain the source identity.
Malformed, ambiguous, unmatched, future, incompatible, or conflicting revisions
fail closed with `LIQUIDITY_CANONICAL_RESOLUTION_FAILED`; the public message stays
redacted.  Historical evaluation still uses only the raw row under its own
timestamp and contract, so materialization creates no hindsight readiness.

Track B feasibility's actual aggregate schema uses `comparable_universe_size`,
`per_family_availability`, `companies_by_usable_family_count` (string keys
`3`/`4`/`5`/`6`), `cross_sectional_sample_sizes`, `missingness_patterns`, and
`future_panel_feasible`.  It does not expose the attempted informal property
names.  The checked-in lifecycle fixture has two comparable companies and five
canonical liquidity observations: after visibility both companies have the
current-assets/current-liabilities minimum and one also has unrestricted cash.
The small fixture is deliberately not evidence for preregistration; the operator
universe counts must be read from the post-merge command below.  Feasibility is
not contract approval, and no contract, score, ranking, candidate, recommendation,
selection, vintage, observation, or validation credit is produced.

### Exact read-only post-merge verification

```powershell
git switch main
git pull --ff-only
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$Historical = "2026-10-04T21:30:00+00:00"
$After = "2026-10-05T00:30:00+00:00"
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
$Commands = @(
  "liquidity-measurement-compatibility-audit",
  "liquidity-evidence-discovery",
  "liquidity-contract-assessment",
  "liquidity-raw-canonical-inventory",
  "liquidity-evidence-gap-assessment",
  "financial-strength-evidence-audit",
  "financial-strength-contract-assessment",
  "track-b-panel-feasibility",
  "comparable-universe-research-readiness"
)
foreach ($Decision in @($Historical, $After)) {
  foreach ($Command in $Commands) {
    python -m app.investment_research_cli $Command `
      --research-db "$Research" --production-db "$Production" `
      --decision-at "$Decision"
    if ($LASTEXITCODE -ne 0) { throw "$Command failed at $Decision" }
  }
}
python -m app.investment_research_cli liquidity-company-preview `
  --research-db "$Research" --production-db "$Production" --decision-at "$After" `
  --qualified-symbol "<EXCHANGE-QUALIFIED-SYMBOL>"
python -m app.investment_research_cli company-investment-factor-preview `
  --research-db "$Research" --production-db "$Production" --decision-at "$After" `
  --qualified-symbol "<EXCHANGE-QUALIFIED-SYMBOL>"
Pop-Location

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne $ResearchBefore) {
  throw "Research changed during read-only verification"
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) {
  throw "Production changed during read-only verification"
}
```

These are verification reads only: do not plan, apply, restore, rematerialize, or
invoke a provider.  The existing completed run requires no rollback and no repeat
apply.

## Urgent post-merge portability correction

The first merged consumer repair was not portable.  On the post-merge Windows
checkout its own focused command reported five failures and fifteen passes: both
idempotent-retry tests, the offline lifecycle test, the exact 187-row historical
test, and the JSON-order test failed.  The bounded resolver diagnostics, once
surfaced internally, showed `source_timestamp_mismatch` for every otherwise valid
controlled row (5 in the small fixture and 187 in the operator-shaped fixture).
Because rejection uses an ordered first-failure taxonomy, later comparisons were
not reached for those rows.  The cause was raw `isoformat()`/`str()` comparison of
equivalent TIMESTAMPTZ representations rather than comparison of UTC instants.
DATE, numeric, redundant-currency, and scale checks had the same portability risk.

The identical retry had a second root cause.  Validation noticed the expected
post-apply research fingerprint, but then rebuilt a fresh plan against the mutated
database.  That rebuild traversed the failing consumer resolver and its exception
was collapsed to `LIQUIDITY_MATERIALIZATION_PLAN_INVALID`.  Retry recognition now
occurs before ordinary fingerprint rejection or plan reconstruction.  It requires
the exact token identity, unexpired token, operation/contract/hash/validator,
decision boundary, evidence-key digest and complete key set, field counts,
completed manifest, zero conflicts, matching revision/canonical pairs, and the
unchanged production fingerprint.  A valid retry returns zero inserted, the
original count unchanged, `idempotent_retry=true`, and performs no write.

Producer and consumer now share canonical serialization helpers for UTC timestamp
instants, DATE values, finite decimal values, null/blank/USD redundant currency,
identity scale, evidence keys, operation identity, decision timestamps, and
canonical availability.  Valid future revisions have their durable lineage
checked but are not substituted before `available_at`; after that instant the one
revision replaces its one raw source.  Individual corruption regressions retain
the bounded reason taxonomy, while the CLI continues to emit only one stable
redacted error and empty stdout on failure.

During the operator verification all listed consumers failed closed, including
both boundaries, discovery, both contract/inventory reports, financial strength,
Track B, comparable-universe readiness, and both previews.  Those attempts were
read-only and changed neither database hash.  Empty failed-command output must not
be converted through `@($null).Count`; it is a command failure, not one ranking,
candidate, or recommendation.  No model output was produced.

The read-only PowerShell sequence above remains the post-merge verification
sequence.  Capture stdout only after checking `$LASTEXITCODE`, require nonempty
JSON before `ConvertFrom-Json`, and stop immediately on an empty report.  The
completed operator run remains valid and needs no rollback, rewrite, or repeat
apply.

### Hotfix merge-resolution status

The conflict resolution retains the semantic-contract resolver and the complete
combined regression history.  It does not record a successful operator
verification: the final PowerShell sequence above remains an operator action to
run only after this hotfix is merged.  Resolving the source conflict neither
accessed nor changed the operator databases or the completed materialization.
