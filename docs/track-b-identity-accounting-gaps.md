# Bounded Track B identity and accounting-gap diagnostic

Implemented against current main `321467a0d48ff7a55e1a561044304b9517c8d6ec`
(merged PR #94), confirmed using GitHub commit metadata. The local source
snapshot was updated from that exact ref before implementation because the
execution environment's git proxy was unavailable. The published change has
that real main commit as its parent; the local snapshot commit is not published.

Command: `track-b-identity-accounting-gap-diagnostic`, through
`python -m app.investment_research_cli`, with the existing `--research-db`,
`--production-db` and aware `--decision-at` arguments.

This is **TRACK B RESEARCH FOUNDATION — NOT A MODEL**. It changes no classification,
eligibility, accounting alias, contract, Track A behavior or prerequisite state.
All eight unresolved preregistration groups remain unresolved, contracts A–D
unselected, sample minima unset, validation credit zero and model/output arrays
empty. There is no acquisition, provider request, payload parsing, ingestion,
schema initialization, resolver invocation, materialization or model execution.

## Evidence and planning inputs

The verified operator attachment `pr94-inventory-post.json` has file SHA-256
`ea6cd82019851489f755210baa83b1108c768bae16dd117304193b938631813f`.
Its inventory boundary is `2026-10-05T00:30:00+00:00`, its population is 500 stored
security IDs, and it records zero certified formulas. It reports 1,322 canonical
current-debt concept mismatches and 6,827 raw exact cash observations, versus zero
observations in the canonical `cash_and_cash_equivalents` field. Those facts are
reference evidence, not live assertions embedded in tests or forced results.

The acquisition plan supplied in the preceding conversation separates historical
membership/identity, actions/proceeds, benchmark total-return metadata and
comparable accounting histories. It requires reconciling 500 inventory IDs with
the existing 71-company comparable roster; none of the 500 is automatically
eligible. It also requires distinguishing exact-concept, period-construction and
provenance gaps before proposing acquisition. This change implements that bounded
diagnostic step only. Every acquisition proposal remains unapproved.

## Identity denominator reconciliation

The research roster is drawn from persisted `security_classification_evidence`
with visible aware public/retrieval/availability timestamps, the existing
`available_at=max(public_at,retrieved_at)` rule, current rows and ordinary-company
classification. It uses the existing classification reader's timestamp/type
population, deduplicated by security ID; it never falls back to numerical
canonical evidence or infers a roster from ticker shapes. An ordinary declaration
with another visible type is retained as an ambiguous roster member. No effective
classification interval is invented. The roster is a diagnostic perimeter, not
an approved eligible population. A company-to-share-class collapse remains
unproven; counts are explicitly security-level until durable identity resolves it.

The 500-style stored denominator is the same union used by inventory: listing,
snapshot-member, SEC-mapping, canonical and raw SEC security IDs. Classification
rows alone do not add IDs to that denominator. Reference counts 500 and 71 are
shown with live deltas; they are never assumed or imposed.

Only exact stored `security_id` equality can match a roster row. CIK is a
consistency check, never a join. Numeric CIK spellings are normalized to ten
digits, preserving the durable security ID. Multiple stored CIKs, malformed CIKs,
shared CIKs requiring unresolved share-class policy and conflicting current
classifications are flagged as ambiguous. All persisted associations are checked,
including conflicting old/post-boundary associations; they are not silently
declared effective at the decision boundary. Absence of a CIK does not invalidate
an exact security-ID match, but its count is reported and issuer identity remains
uncertified. No CIK-only match, ticker/name fallback or inferred effective mapping
is allowed.

Roster matched + ambiguous + unmatched counts reconcile to the full roster.
Stored matched-roster + ambiguous-roster + outside-perimeter-by-current-
classification + outside-roster/perimeter-unresolved counts reconcile to the full
stored denominator. **Outside the roster is not proof of outside the perimeter.**
Unclassified stored IDs remain unresolved; they are not assigned to an approved
exclusion group. Full hashed roster dispositions are returned up to 256 IDs.
Current identity matches never prove historical membership, effective issuer
intervals, delistings or ticker-reuse protection.

## Accounting diagnosis

Only unambiguous matched research roster IDs enter the accounting denominator.
Ambiguous/unmatched IDs are withheld, not assigned zero inputs. Research and
production are reported separately against that same explicit roster; their raw
and canonical counts are never added or substituted. Production observations are
exact-ID evidence diagnostics, not certification of a production issuer mapping.

For each required value/financial-strength field, each layer reports exact stored
concept absence, field absence, existing observations rejected by the unchanged
draft adapter, primary rejection reasons, post-boundary state, independent missing
provenance flags, and stored duration shapes. Independent provenance checks run
even when the adapter's first rejection is a concept mismatch. Unknown/view/
incompatible source schemas yield null absence/coverage counts, not zero.

Raw diagnostic review buckets include `LongTermDebtCurrent`, `ShortTermDebtCurrent`,
`CommercialPaper` and explicitly broader/restricted cash concepts. These buckets
expose stored-but-rejected evidence; they do not expand accepted concepts. The
draft still accepts only `ShortTermBorrowings` for current debt. The report returns
bounded original-concept counts explaining canonical current-debt mismatches,
both across stored evidence and inside the matched roster, and compares the live
total with the inventory's 1,322 reference. It cannot identify the actual original
concept composition from the aggregate attachment alone; operator execution is
required. Other existing alias contracts may permit a concept that this narrower
draft rejects. No alias, debt substitution or component aggregation is activated.

Raw exact `CashAndCashEquivalentsAtCarryingValue` and canonical exact-field cash
coverage are separate. The report also counts the exact concept stored under
other canonical fields, including `unrestricted_cash` if present, without treating
that as canonical draft cash. Missing canonical cash does not imply missing SEC
cash. Raw facts never become canonical evidence through this command.

Quarter shapes remain 60–120 days; annual shapes 330–400 days. A 150–300-day
duration is labeled a cumulative-shape **candidate**, not certified YTD. Shared
starts with multiple endpoints and shared ends with multiple starts are counted.
No fiscal calendar is invented, no cumulative subtraction is performed and no
quarters are derived. Gaps/overlaps compare neighboring distinct accepted quarter
shapes; metadata duplicates are deduplicated for period structure only, never for
numerical revision precedence. A gap before/after available history cannot be
measured without an approved expected history window.

Value requires four contiguous identical OCF/capex standalone-quarter periods;
asymmetric period endpoint sets are reported. Financial strength requires four
standalone OCF quarters and aligned current-debt/noncurrent-debt/exact-cash
instants at the chain endpoint. Missing aligned instants are counted by field.
The draft accounting construction is unchanged. Market-cap denominators, values,
staleness/materiality, reorganization comparability and the accounting contract
remain uncertified. Issue counts are explicitly nonexclusive and are accompanied
by deterministic samples of at most ten matched identities per layer.

## Completed controlled SEC retrievals

The diagnostic recognizes stored matching run/checkpoint identities across run,
lineage, plan, operation type, contract version and concept hash, completed status,
successful transaction and exact retained `submissions` plus `companyfacts`
endpoint metadata for the same security and CIK. Two unrelated endpoint names do
not establish completion. No payload JSON is selected. This recognizes declared
completion without recertifying payload hashes, parser completeness or accounting
coverage. Historical completion additionally requires aware endpoint retrievals
and checkpoint update timestamps at/before the decision boundary; current
completion is not backdated.

Missing exact inputs are partitioned into completed-retrieval and completion-
metadata-unavailable groups. Missing concepts or canonical fields after a
completed controlled retrieval call for later stored-payload/parser/mapping
diagnosis, not another identical provider retrieval. This command recommends no
repeat retrieval, including when completion metadata is absent. Estimated provider
requests remain zero. Completion never resolves accounting gaps or readiness.

## Bounds and operator verification

Nine fixed base-table adapters use explicit metadata projections. Unknown tables,
views, numerical amounts, prices, model/outcome tables and provider payloads are
not read. Each table has a 500,000-row cap; cells are checked against 1,024
characters before collection; fetch batches contain at most 2,048 rows. Dedicated
connections use `read_only=True`, 128 MB SQL memory, one thread and no disk spill.
Period state is capped at 50,000 per security/field, and existing chain work at
50,000 per call. The compact deterministic UTF-8 report cap is 128 KiB. A resource
exhaustion fails closed, never samples aggregate counts. File hashing/full scans
remain proportional to input size; no fixed wall-clock bound is claimed.

Both database files are SHA-256 fingerprinted before and after. Both after hashes
are attempted independently in finally, including read/query/processing failures.
No report is published if a baseline/after hash is unavailable or changed. The
public CLI retains its stable redacted nonzero error envelope, with the new domain
code `TRACK_B_GAP_DIAGNOSTIC_FAILED` for diagnostic bounds/hash failures.

Run the complete **Windows PowerShell 5.1** script
`scripts/track-b-gaps-verify.ps1` from the checked-out PR branch, after stopping
concurrent writers. Its optional `-Repo` parameter overrides the established
operator repository path. It does not pull, switch branches or modify databases.
It runs relevant offline regressions; independently verifies both established
boundaries, deterministic repeats, exact population reconciliations, bounds and
all zero-output/unresolved contracts; and attempts both external after hashes in
finally. It writes compact UTF-8 (without BOM) `track-b-gaps-0.json`,
`track-b-gaps-1.json` and `track-b-gaps-verification.json` under
`backend/data/research/reports`. A failed stage is recorded with a fixed reason
code and nonzero exit, without raw exception/path/payload output. Interpret saved
diagnostic files together with the final external-hash verification report.

Development uses only synthetic temporary databases, including poison numerical
and payload columns. Operator counts remain unmeasured during development.
Passing offline tests or the extracted Python verification portion is not
Windows PowerShell 5.1 execution or operator verification. Retrospective public
availability and actual operator possession remain separate; evidence retrieved
now never gains historical system availability through this diagnostic.

## Confirmed operator failure and verifier diagnostics

Operator execution of PR #95 at `c058f72` passed offline tests but emitted
`DIAGNOSTIC_VERIFICATION_FAILED` at both boundaries. Both external database
fingerprints remained unchanged. This confirms failed verification with unchanged
files; it does **not** identify a command, parsing, schema, assertion, limit or
repeat failure. No successful operator verification is claimed.

The original script caught all exceptions around command execution, UTF-8/JSON
parsing, validation, repeat execution and report saving, returning the same code.
It saved the diagnostic JSON only after all checks passed, discarding failed
stdout/stderr. The new script retains captures before validation and reports fixed
stage/reason codes. All 27 original assertion expressions are unchanged, confirmed
by AST comparison; no diagnostic rule or limit has changed.

Use `scripts/track-b-gaps-verify.ps1 -Repo "C:\Users\Juan Estrada\Projects\SignalLens" -InspectOnly`
to inspect saved `track-b-gaps-0.json` and `track-b-gaps-1.json`. This runs neither
tests nor diagnostic commands. It checks old report assertions using their own
recorded baseline context, reports the count of baseline hashes matching current
files, and independently checks current external hashes in finally. A saved report
may belong to an earlier run; passing its checks is not verification of the failed
run or proof of deterministic repeats. Missing files get `SAVED_REPORT_MISSING`.

If reports are missing, use the same script with `-SkipOfflineTests` instead.
This runs only the two read-only diagnostic boundaries and deterministic repeats.
The test suite is not rerun. First and repeat stdout/stderr are saved as UTF-8
without BOM under `backend/data/research/reports`, using
`track-b-gaps-0.stdout.txt`, `track-b-gaps-0.stderr.txt`,
`track-b-gaps-0.repeat.stdout.txt`, `track-b-gaps-0.repeat.stderr.txt` and the
corresponding `1` files. Raw captures remain local, may contain interpreter paths,
and are never printed. Invalid UTF-8 is retained as replacement characters in
the text captures; strict decoding of the original bytes still fails validation.
The safe public CLI error envelope is unchanged. Only allowlisted public error
codes are printed; exception text, public-error messages and report values are not.

| Stage/reason | Meaning |
| --- | --- |
| `.command / COMMAND_LAUNCH_FAILED` | The process could not be started. |
| `.command / COMMAND_NONZERO` | The diagnostic returned a nonzero exit; captured stderr is retained locally. |
| `.command.capture / CAPTURE_SAVE_FAILED` | Captures could not be saved. |
| `.parse / UTF8_DECODE_FAILED` | Original stdout bytes are not strict UTF-8. |
| `.parse / JSON_PARSE_FAILED` | Stdout is not parseable JSON. |
| `.schema.<fixed check> / REPORT_SCHEMA_INVALID` | Required keys/types/shapes are invalid. |
| `.validate.<fixed assertion> / REPORT_INVARIANT_FAILED` | The named unchanged assertion failed. |
| `.validate.output_limits / OUTPUT_LIMIT_OR_SIZE_FAILED` | The unchanged byte-count/128-KiB contract failed. |
| `.repeat.command / COMMAND_NONZERO` | Repeat execution failed, distinct from initial command failure. |
| `.repeat / DETERMINISTIC_REPEAT_MISMATCH` | Successful first/repeat stdout differ. |
| `.save / REPORT_SAVE_FAILED` | A verified report could not be saved. |

Named invariant stages identify command, decision boundary, read-only metadata,
zero outcomes/model, persistence/acquisition, eligibility/readiness, accounting,
contracts, requests/credit, blocker count/state, empty outputs, size limits,
roster/stored partitions, roster details, historical/effective identity,
internal/external fingerprints, layer denominator, certified formulas, sample
population/return/truncation, coverage partition and repeat recommendation.
Schema stages are separate from these invariant stages. Safe events are bounded
by fixed control flow; offline fixtures assert at most 64 events and under 16 KiB
of summary output. Full raw captures are not echoed into chat or terminal output.

Verifier-only offline tests reproduce the original opaque exception envelope and
independently inject command, launch, UTF-8/JSON, schema, invariant, output-contract,
repeat and save failures. They verify focused modes never invoke pytest, inspection
never invokes the diagnostic, failures retain captures, and both after hashes are
checked. These tests reproduce the confirmed loss of failure evidence, **not** an
unconfirmed operator exception. The actual operator failure requires the safe
inspection result before a specific cause can be reproduced or repaired offline.
