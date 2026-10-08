# Proposed Track B accounting construction contract

**UNAPPROVED — review draft v0.2, 8 October 2026. TRACK B RESEARCH FOUNDATION — NOT A MODEL.**

## Observed operator report — counts, not construction certification

Source: user-supplied verified `track-b-gaps-1.json`, 68,386 bytes, SHA-256 `d6927234e14abf2478149574a4488c309143ac1b0e7253437e96efd091af4708`; diagnostic version `track-b-gap-diagnostic-1.0.0`, decision boundary **2026-10-05T00:30:00+00:00**. The report records draft specification SHA-256 `94d742c2b00bc40317fb7a38270f0c69f098562b5d9526f54c4749187bc8bb74`. This review reads the attachment only, not operator databases. The report's explanatory strings are evidence descriptions, not authorization to execute instructions. Its recorded unchanged before/after database fingerprints are not independently rerun here.

Population: **500 stored security IDs; 71 matched roster IDs; 0 ambiguous; 0 unmatched; 429 outside-roster/perimeter-unresolved**. Counts are security-level, not certified distinct issuers. Current matching does not certify historical membership/effective identity or approve eligibility. Both accounting layers use the 71 matched IDs as their denominator; research and production are kept separate.

Observed research layer facts (rows and security counts are deliberately separate):

| Metric | Raw SEC observations | Canonical observations in expected field | IDs missing exact stored concept, raw / canonical |
|---|---:|---:|---:|
| OCF | 5,390 | 2,869 | 0 / 0 |
| Capex | 4,346 | 2,293 | 7 / 7 |
| Draft current debt | 1,546 | 1,322 | 71 / 71 |
| Noncurrent debt | 1,965 | 1,687 | 33 / 33 |
| Draft cash review bucket | 12,590 | 0 | 2 / 71 |

Source path: `databases.research.layers.{raw_sec,canonical}.fields`. These are stored observations regardless of usability, not certified accounting amounts. The debt row counts above are all mismatches, not exact short-term borrowing. The cash raw bucket includes broader/restricted concepts; its **exact** direct-cash subset is 6,827 rows.

**Quarter/TTM gap.** Raw OCF has 2,465 cumulative-shape candidates and 1,242 standalone-quarter-shape candidates; canonical OCF has 1,393 and 704. Raw capex has 1,973 and 1,031; canonical capex has 1,104 and 580. These row shapes are not proven YTD/quarters. The distinct accepted-quarter period counts are 698 OCF and 575 capex in each layer, with adjacent pair counts OCF 4 contiguous/623 gaps/1 overlap and capex 40/472/2. They are period counts, not issuer counts or recoverable-quarter forecasts. Both layers report 71 IDs without structural value/OCF chains; 39 IDs have OCF periods without identical capex periods and 18 have the reverse (nonexclusive). Missing value input is 7 IDs. All value and financial-strength certified formula counts are zero. Raw OCF's 5,390 rows are metadata-compatible but lineage/identity/values remain uncertified; capex has 4,342 such rows and 4 rejected for missing comparable-period metadata. Hence stored flow evidence plus an unsupported cumulative-construction route differs from true capex absence for 7 IDs. This proposal does not claim subtraction would repair all 71 chains.

**Debt gap.** `databases.research.concept_explanations.current_debt` identifies all 1,322 canonical mismatches (also inside the matched roster) as `LongTermDebtCurrent`, matching the prior inventory reference with delta zero. The raw review bucket contains 1,546 rows of that same concept. Exact `ShortTermBorrowings` is missing for all 71 IDs; the draft current-debt field/bucket itself is missing for 34, while 37 have stored but unsupported evidence. Long-term current maturities are therefore observed, but borrowing amount/overlap/completeness is not certified. Raw financial-strength missing-input count is 35; canonical is 71. Those nonexclusive family counts cannot be added to field absence counts.

**Cash gap.** `databases.research.concept_explanations.cash` reports 6,827 raw exact `CashAndCashEquivalentsAtCarryingValue` rows, all inside the matched roster; 0 expected-field canonical rows; and 65 same-concept canonical rows under `unrestricted_cash` (row counts, not 65 companies or necessarily validated facts). Exact-concept raw presence spans 69 IDs, with 2 missing; the broader raw cash bucket is missing for 1 ID. Of raw cash bucket rows, 5,763 are concept mismatches, 6,826 are metadata-compatible but uncertified and 1 lacks unit/currency. Thus 69 raw-only exact-cash IDs are a field-contract gap, not raw-source absence, while 2 lack the exact stored concept. No source/canonical copies are summed.

**Production is a separate absence observation.** Its raw/canonical source tables are reported as `absent_evidence`; each required field has 0 stored observations and 71 IDs missing field/exact concept. Those zeros describe this report's production layers, not research absence or absence at SEC. Endpoint-alignment counters of zero are conditional on finding OCF chains; with no chains they do not prove alignment.

**Completed retrievals and unchanged controls.** `controlled_sec_retrievals` records 71 completed currently and by the boundary, all 71 in the matched roster; 71 missing exact raw inputs after completed retrieval and 0 without completion metadata. Completion is declared matching transaction/endpoint metadata, not recertified payload integrity/parser completeness. Repeat retrieval is explicitly false; estimated requests and validation credit are zero. The report records all eight blockers unresolved, Contracts A–D unselected, sample thresholds null and model/output arrays empty. No repeat SEC retrieval is proposed; any later gap diagnosis must inspect already retained payload/parser/mapping evidence under separate authorization.

## Proposed rules and synthetic illustrations — explicitly unapproved

Everything below is a review proposal, not a report observation or activated definition. Each worked-example amount is invented; passing offline examples supplies no operator evidence or validation credit. Development uses synthetic evidence only. The isolated operator feasibility command permits bounded read-only inspection of stored evidence and reports counts; it makes no provider requests, derives no accounting amounts and persists no derived evidence. No aliases, consumer or Track A changes, Contracts A–D selection, eligibility decisions, model outputs, return analysis or coverage optimization occur. Local code context: `docs/track-b-identity-accounting-gaps.md`, the unchanged draft panel and the separate liquidity measurement/mapping contract. These proposals are not optimized to increase the observed counts.

## Common prerequisites and refusal vocabulary

Each input must have an exact durable security ID and certified issuer association for its period (CIK alone is not a join); source fact ID; accession and source locator; exact taxonomy namespace/version/concept; duration or instant context; actual dates; dimensions/consolidation scope; source amount, currency, unit, scale and sign convention; filing/public timestamp, retrieval timestamp and availability timestamp; and amendment/revision relationships. Use finite Decimal amounts. Preserve the source representation and losslessly normalize known positive scales to USD base units; reject currency contradictions, unknown scales and FX substitution. This proposal does not amend existing validator scale rules.

Availability is adapter-specific; this proposal does not rewrite established raw/legacy rules. For stored SEC `sec_facts`, input availability is `max(public_at, retrieved_at)` using aware timestamps. Legacy canonical rows retain their existing `available_at = max(public_at, retrieved_at)` rule; their recorded materialization time is not retrospectively inserted into that legacy rule. Other legacy adapters retain their own rules and are not silently treated as SEC facts.

Controlled canonical liquidity inputs, identified by their stored operation provenance, require `available_at = max(public_at, retrieved_at, materialized_at)` with all three timestamps aware. A missing/invalid control marker or required timestamp is unproven; it is not permission to downgrade a controlled input to legacy. Each input must satisfy its applicable rule and have input availability at/before decision D.

Three times remain separate: (1) the maximum visibility time of the selected inputs, (2) the actual time the feasibility assessment/calculation runs, and (3) the availability of any future persisted canonical revision. An offline assessment executed now may describe inputs visible at an earlier D, but its output did not exist then. No derived amounts are produced or persisted by this assessment. If a future separately authorized producer creates a canonical revision, its availability must include the actual production/materialization time and all input availability times; it must never use an old decision boundary to backdate newly produced canonical evidence. Future persisted-revision availability is therefore at least `max(all input available_at, actual materialized_at)` and must also obey that producer's public/retrieval contract. Historical public disclosure, a period end or filing date without time alone does not establish system possession. Unknown visibility fails closed.

Synthetic visibility example: raw public/retrieval times are 1/2 August, so the raw input can be visible on 11 August. A controlled canonical copy materialized on 12 August is invisible on 11 August even if its source was visible. Reporting its availability as 2 August is invalid. A legacy canonical row whose established availability is 2 August retains that rule. A calculation executed on 8 October using August-visible inputs is an October assessment; any future canonical revision created on 8 October cannot become available in August.

Select revisions using explicit fact-level supersession and compatible accounting basis visible at D. An amendment does not supersede every fact merely by being newer. Equivalent repeated disclosures may be deduplicated with all references retained; incompatible unresolved values or bases cause refusal. Never backdate later corrections. Cross-accession subtraction requires documented compatibility, not just matching tag strings.

Record separate flags, which may coexist:

| Finding | Meaning and consequence |
|---|---|
| `MISSING_STORED_EVIDENCE` | Required fact or prerequisite metadata is absent from the specified layer/snapshot. Not zero and not proof that SEC never reported it. |
| `UNSUPPORTED_DRAFT_CONSTRUCTION` | Relevant evidence exists, but the unchanged draft does not accept the concept, field or arithmetic route. Not source absence. |
| `INCOMPATIBLE_OR_AMBIGUOUS` | Available inputs cannot establish one compatible measurement. Refuse calculation. |
| `NOT_VISIBLE_AT_DECISION` | Evidence exists only after D. No historical substitution. |
| `PROPOSED_OFFLINE_ONLY` | Synthetic prerequisites pass this proposal; no operator/canonical acceptance is implied. |

Unknown source schemas or unavailable report evidence yield unknown findings, not zero absence counts. A metadata-only gap diagnostic cannot establish amount/sign, revision or full-context compatibility.

## 1. Standalone-quarter and TTM OCF/capex

**Meaning.** OCF is signed net cash provided by/used in operating activities, exact concept `NetCashProvidedByUsedInOperatingActivities`. Capex is gross cash payments to acquire PP&E, exact concept `PaymentsToAcquirePropertyPlantAndEquipment`, represented as positive expenditure magnitude after documented source sign normalization. Exclude acquisitions, net investing cash, sale proceeds and broader asset purchases. Never apply absolute value to an unexplained negative capex fact. Neither concept receives a new alias.

**Exact prerequisites.** In addition to common prerequisites, require an evidenced fiscal calendar with actual quarter/year boundaries, fiscal-year start, and YTD/annual nature. A 150–300-day shape is only a cumulative candidate. The existing 60–120-day quarter and 330–400-day annual checks are guardrails, not calendar proof. A documented 52/53-week year is allowed only with explicit boundaries and disclosure; do not invent calendar quarters or divide an annual amount by four. Transition/stub years remain unsupported here.

For each metric X and fiscal year y, define cumulative `C(y,k)` from the identical fiscal-year start through quarter k, with `C(y,4)` the compatible full year. Require identical concept meaning, currency, normalized unit, dimensions, consolidation and accounting basis. An acquisition is not automatically disqualifying, but changes in presentation, discontinued operations or reorganization require evidence that both operands share one basis; unresolved comparability is refused.

`Q(y,1) = C(y,1)` only when the duration is exactly the first fiscal quarter. For k=2,3,4, `Q(y,k) = C(y,k) − C(y,k−1)`. Both operands start on the same fiscal-year date and end at evidenced adjacent quarter boundaries. A direct standalone quarter can be retained separately; if it conflicts beyond source precision with a constructed quarter on the same basis, refuse pending reconciliation. Preserve rounding uncertainty; do not introduce a numerical reconciliation tolerance without review.

TTM at quarter end t is the sum of **four distinct, consecutive, nonoverlapping fiscal quarters** on one compatible basis. Quarter start equals the day after the previous end. OCF and capex must have identical four start/end pairs for the paired value construction; financial strength needs the OCF chain and its separately aligned instants. An equivalent bridge is `FY(previous year) + YTD(current,k) − YTD(previous,k)` only when it represents exactly that four-quarter chain and all three operands are compatible and visible. If both routes exist, retain both lineages and reconcile; do not pick the larger number or higher-coverage route. No annualization, missing-quarter zero-fill or mixed cumulative/standalone sum.

**Refuse** missing predecessor/full year; inferred YTD status; incompatible fiscal calendar; gap/overlap; mismatched OCF/capex endpoints; unknown sign/scale; unresolved recast; post-D input; mixed consolidated/segment values; or missing expected-window/staleness policy for consumer eligibility. A mathematically valid chain is not certification of sufficient history or freshness.

**Provenance.** Retain the common input records, fiscal-calendar evidence, operand order/coefficient (+1/−1), normalization and sign transforms, revision decisions, output start/end, input availability maximum, rounding interval, proposal version and refusal reasons. A constructed quarter is explicitly synthetic, never a reported SEC fact.

**Synthetic example (USD millions, calendar fiscal year, compatible consolidated basis).** Prior Q4 OCF/capex is 20/8. Current YTD Q1, Q2, Q3 are OCF 30,70,90 and capex 10,25,40. Standalone Q1–Q3 are 30/10,40/15,20/15; TTM through Q3 is OCF 110, capex 48. If prior FY is 100/40 and prior Q3 YTD is 80/32, the bridge also gives `100+90−80=110`, `40+40−32=48`. Current FY 130/60 yields Q4 40/20. An amendment makes Q2 YTD OCF 75 and recasts Q1 to 35: Q2 stays 40. Mixing 75 with unrecast 30 incorrectly gives 45 and is refused. If that amendment was public 10 August but retrieved 12 August, it is unavailable at a 11 August decision.

## 2. Short-term borrowing, current long-term maturities and debt totals

**Meaning.** `ShortTermBorrowings` identifies short-term borrowing; `LongTermDebtCurrent` identifies current maturities of long-term debt. Neither is an alias for the other. The unchanged draft's `current_debt` accepts only `ShortTermBorrowings`; reject `LongTermDebtCurrent` for that slot as an unsupported draft mapping, not missing debt evidence. Name proposed components `short_term_borrowings`, `current_maturities_long_term_debt` and `noncurrent_long_term_debt` without activating fields.

**Exact prerequisites.** Require common provenance plus exact aligned balance-sheet instant, nonnegative carrying amounts, and disclosed component coverage. A candidate borrowing total is `ST + LTD_current + LTD_noncurrent` only with evidence of exhaustive, mutually exclusive coverage for the defined borrowing perimeter. Tag names alone do not prove disjointness. Establish whether ST includes commercial paper/current maturities, whether long-term aggregates include current portions, and whether leases, overdrafts or other obligations are included. The lease/debt perimeter remains an explicit unapproved choice; no generic total-debt certification is supplied here.

Use a disclosed inclusive total as an alternative representation, never as another addend. An inclusive long-term total cannot be added to its current component. Commercial paper cannot be added to ST if already included. Require reconciliation to a disclosed total where available, with precision accounted for; disagreement or unknown coverage causes refusal. Missing components cannot be replaced by zero without explicit zero/absence evidence. Use the same endpoint for cash and OCF-chain alignment; no latest-nearest-date substitution.

**Provenance.** Add a component-inclusion graph or source-note coverage table, exact instant, reconciliation references and explicit exclusions. Each underlying obligation enters a total at most once. Retain alternative representations without stacking them.

**Synthetic example.** ST 12, current long-term maturities 8 and noncurrent long-term debt 80 are documented disjoint: borrowing total 100. Inclusive long-term total 88 gives the alternative `12+88=100`; `12+88+8=108` is refused. Commercial paper 5 already within ST is not added. If ST is absent but maturities 8 exist, the draft ST slot remains missing; substituting 8 is unsupported. A reported current-debt aggregate 20 plus noncurrent 80 can support a separate proposed total only if its exhaustive 12+8 scope is evidenced; it cannot be added to those children.

## 3. Direct cash evidence and unrestricted_cash

**Direct route.** Exact `CashAndCashEquivalentsAtCarryingValue` is a reported cash/cash-equivalents instant, not a TTM flow. Require common provenance, supported taxonomy, nonnegative USD amount and the exact accounting endpoint. Exclude combined cash/restricted-cash totals and marketable securities. Missing canonical `cash_and_cash_equivalents` does not mean missing raw SEC cash. A direct fact stored under another canonical field does not populate the draft field automatically.

**Separate canonical contract.** Existing liquidity code associates that exact concept with `unrestricted_cash` through its own versioned measurement/mapping contract; preserve its validation, lineage and consumer semantics. Acceptance there does not certify draft canonical cash, aliases, unrestricted availability for every use, or general coverage. Retain both source identity and canonical field/contract identity when discussing overlap. Never count raw and canonical copies of the same fact as two assets.

A possible `combined cash − restricted cash` construction is a separate, unapproved route, not direct exact-concept evidence. Require exhaustive restricted cash and restricted cash equivalents covering both current and noncurrent portions, aligned instant, scope, basis, scale, currency and visibility; document inclusion in the combined total. `RestrictedCashAndCashEquivalentsCurrent` alone is insufficient when noncurrent restricted amounts are unresolved. Refuse ambiguous restriction coverage, negative residuals, unavailable components or adding short-term investments. No component derivation/persistence is authorized here.

**Provenance.** Direct route retains exact source fact and mapping/validation version separately; proposed residual retains every component and inclusion proof, coefficients, endpoint, availability maximum and explicit `constructed_not_direct` status.

**Synthetic example.** Exact direct cash 50 exists raw and under `unrestricted_cash`, but draft `cash_and_cash_equivalents` is absent: direct evidence present, canonical draft field missing, cross-field substitution unsupported. Combined cash 65 includes restricted current 10 and noncurrent 5; complete compatible coverage permits proposed residual 50. If only current 10 is known, 55 is refused. Short-term investments 7 do not make direct cash 57. Later retrieval of the restricted component cannot backdate the residual.

## Review disposition and offline validation

Validation command from repository root: `python -B backend/tests/test_track_b_accounting_construction_proposal.py`. The proposal tests are a synthetic design oracle, not an application resolver. Separate feasibility and verification-runner tests use temporary synthetic databases/files only. Full filing semantics, revision selection, precision and component completeness require retained documentary metadata; missing metadata remains unproven.

All eight existing preregistration groups remain **unresolved**:

1. `accounting_contract_unapproved`
2. `historical_universe_and_identity_unproven`
3. `sample_policy_and_minima_unapproved`
4. `aligned_history_and_staleness_unproven`
5. `outcomes_and_benchmark_unapproved`
6. `execution_costs_unapproved`
7. `temporal_splits_and_inference_unapproved`
8. `registration_and_holdout_controls_unlocked`

Contracts A–D remain unselected, sample minima unset, validation credit zero, and model/output arrays empty. Next review must assess the proposed semantic choices and source prerequisites. The supplied report has been reconciled above; it certifies no new construction and requires no repeat of completed SEC retrievals.

## Isolated proposed feasibility assessment and operator verification

`app.track_b_construction_feasibility` is a separate module entry point. It is not registered with existing consumer commands and never calls a resolver, producer or provider. It reads six fixed base tables: the four existing identity/classification tables plus `sec_facts` and `canonical_factor_evidence`. It uses the existing exact-ID reconciliation to select the matched research roster; production is assessed separately against that same perimeter. Views/unsupported schemas yield unknown counters (`counts: null`), not absence zeros. Missing base tables yield observed absence in that layer only.

The module computes counts, not financial amounts. Numeric stored columns are inspected inside SQL for finite-value signatures, sign and relative order; no amounts are returned to Python or subtracted/summed. Stored value disagreements are not automatically called conflicting accounting revisions. No payload, outcome, price or model tables are read. Optional retained proof fields are a proposed adapter only: no tables/columns are created or populated, and no new evidence is inferred from existing FY/FP/frame labels, amendment flags, duration lengths, matching tag names or equal numbers.

The required proof fields are:

| Prerequisite | Explicit retained metadata required |
|---|---|
| Fiscal calendar | `fiscal_year_start`, `fiscal_quarter`, all four `fiscal_quarter_end_*` dates, `duration_kind` (`ytd` or `standalone`), `fiscal_calendar_source`, `fiscal_metadata_available_at` |
| Context | `context_scope` (`consolidated` for candidates), `context_dimensions` as explicit JSON object (including explicit `{}`), `accounting_basis`, `context_source`, `context_metadata_available_at` |
| Revisions | `revision_set_id`, `revision_status` (`current_compatible`, `superseded`, `conflicting`), `revision_source`, `revision_metadata_available_at` |
| Precision | `source_decimals` (integer −30 to 30 or `INF`), `precision_source`, `precision_metadata_available_at` |
| Borrowing scope | `component_members` as unique mutually exclusive balance-portion IDs (instrument names alone are insufficient), `borrowing_universe_members` as the exhaustive obligation-ID set, `borrowing_scope_source`, `borrowing_metadata_available_at` |

Proof timestamps must be aware and at/before D. A later retained proof cannot support a historical candidate. A source reference is a recorded declaration, not independent documentary certification by this command. The checked-in ingestion schema does not retain these complete proof sets; those omissions therefore produce exact missing/unproven counts, not assumed fiscal/context/component compatibility. Null source scale in the established USD raw representation is identity scale; only identity representations are compared here. Nonidentity scales remain incompatible in this implementation, even though a broader lossless-scale proposal remains available for review.

Controlled canonical operation markers are projected from stored provenance inside SQL. Invalid/missing provenance is unproven. Legacy acceptance is limited to the established `milestone-37-audited-alias-contracts-1` contract; unknown contracts are not reclassified as legacy. Controlled input visibility includes actual `materialized_at`. This assessment does not recertify controlled revision/run integrity or replace the existing resolver.

Report counters use these units and limits:

- `row_counts` partitions relevant stored rows into visibility-unproven, post-decision, future-period and visible rows. Metadata missing/unproven counts are nonexclusive flags over visible rows, with additional visibility-failure counts. Exact concept counters are row counts, not company counts.
- Flow candidates are distinct routes indexed by security, exact metric, compatible context/revision set, calendar and quarter. First-quarter YTD identity and reported standalone quarters are separate counters. Cumulative subtraction candidates require an adjacent compatible predecessor; decreasing capex cumulative pairs are withheld using SQL relative order. TTM candidates count distinct four-period chains; paired chains require identical context/revision basis and periods. These are prerequisite-compatible candidates, not computed or certified formulas. Annual/YTD bridge arithmetic is not separately evaluated, and no duplicate route is added to TTM counts.
- `shape_*_pairs` count neighboring distinct 60–120-day duration shapes without certifying them. `fiscal_*_pairs` count neighboring candidate periods with retained calendar/context/revision/precision prerequisites. Neither measures gaps before/after the observed history without an approved expected history window.
- Stored value disagreement groups, explicitly declared conflicting revision groups and unresolved multiple-revision groups are separate nonexclusive counters by security/concept/start/end. No newest-filing heuristic or invented precision tolerance is used. Multiple differing values are withheld even when their economic conflict remains unknown.
- Borrowing counters use security/instant groups for the proposed three components. Proven overlap/disjointness of retained component sets is reported independently of proven/unknown completeness of the defined borrowing universe. Missing components are not zero-filled. Repeated equivalent disclosures are not additional obligations. Commercial paper and inclusive long-term aggregates are review concepts, never extra addends.
- Direct cash candidate rows retain only exact `CashAndCashEquivalentsAtCarryingValue` meaning. Exact rows under `unrestricted_cash` are counted separately without promotion to draft cash. Broader/restricted cash is not accepted as direct cash. Unknown context/revisions/precision withhold compatible-candidate status, while exact source presence remains visible in its separate count.

The caps are 500,000 rows per table and across both databases, 1,024 characters per projected metadata cell, 50,000 rows per period group, 50,000 work units per layer, and 128 KiB compact UTF-8 report bytes. SQL uses a dedicated read-only connection with 128 MB memory, one thread and no disk spill. Any exceeded cap or hash failure fails closed; counts are never sampled to fit a bound. Both database before/after hashes are attempted independently, including failures. No successful report is returned if either fingerprint is missing or changed. These are explicit work/data caps, not a fixed wall-clock guarantee.

For Windows PowerShell 5.1, check out PR #96's branch and stop concurrent database writers, then run:

```powershell
& .\scripts\track-b-constructions-verify.ps1 -Repo "C:\Users\Juan Estrada\Projects\SignalLens"
```

The complete script runs only the three relevant offline test modules, then assesses the two established boundaries (`2026-10-04T21:30:00+00:00` and `2026-10-05T00:30:00+00:00`) and repeats each to check counts/invariants. Execution timestamps remain in every saved report; only those timestamps and the resulting serialized byte-count differences are excluded from repeat equality. It saves UTF-8 without BOM `track-b-constructions-0.json`, `track-b-constructions-1.json` and `track-b-constructions-verification.json` under `backend/data/research/reports`. Report files are diagnostics, not persisted canonical/derived accounting evidence.

`-SkipOfflineTests` skips tests only; `-ResearchDb`, `-ProductionDb`, `-Python` and `-Reports` override paths. Both external before/after fingerprints are checked independently in cleanup, including test, command, parse, invariant, repeat and save failures. Command output and reports have byte caps. A failure summary returns nonzero and fixed safe reason codes; raw exception text/payloads are not printed. Diagnostic reports are saved only after the external hashes pass. Existing files from an earlier run may remain after failure: interpret them only with the latest successful verification summary and matching fingerprints/boundaries.

Development validation: 52 focused proposal/feasibility/verifier tests passed; related gap, history and source-integrity regressions also passed. All development fixtures are synthetic temporary files. Exercising the exact embedded Python payload is not Windows PowerShell 5.1 execution or operator verification. The existing attached gap report does not contain the proof/value-signature information needed for new feasibility counts. Actual operator feasibility counts remain unmeasured until this isolated verifier is run against the stored databases; the historical observed counts above are not relabelled as feasibility results. All constructions and eight preregistration groups remain unapproved/unresolved.
