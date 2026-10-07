# PR #92 Windows timestamp regression repair

Baseline: merged PR #92, main `0ddc4b22256f6521ea85bc8d788467d6b2ea0e35`.

The operator reported 64 passed and two failed. Its harness stopped before the
inventory commands ran; both database hashes remained unchanged. This is not
successful operator inventory verification. Operator inventory remains pending.

Both reported failures were reproduced offline with `TZ=Europe/London` using
synthetic temporary databases (DuckDB 1.5.6, Linux). They are test defects, not
evidence of a production UTC-naive adapter defect:

* The market fixture converted TIMESTAMPTZ to TIMESTAMP with a plain cast. DuckDB
  uses the session timezone for that conversion: `2026-10-05 00:30 UTC` becomes
  naive `01:30` in London. The market adapter correctly interprets naive stored
  timestamps as UTC, so that incorrectly converted row appears one hour future.
  Convert explicitly with `timezone('UTC', retrieved_at)` to match the producer's
  `global_universe.utc_naive()` contract. Keep the expected visible count at one.
* Liquidity `2026-10-02 19:16:00+01:00` and
  `2026-10-02 18:16:00+00:00` are the same instant. Compare using the existing
  `liquidity_canonical_contract.timestamp_text()` normalization, checking that
  normalization succeeds. Missing/naive timestamps are still rejected.

Each affected test now explicitly runs under UTC and Europe/London. A scoped
fixture sets the timezone on every fixture and consumer DuckDB connection;
host timezone cannot silently substitute for the requested session. The market
test demonstrates the plain-cast shift, checks corrected storage against the
real UTC-naive helper, includes equality and excludes one microsecond future,
and retains incompatible-schema versus absent-evidence assertions. The exact
187-revision lifecycle retains all counts and verifies historical invisibility,
exclusion one microsecond before materialization, exact boundary inclusion and
later visibility, plus immutable reads and zero model outputs.

No production code or point-in-time validator changes are needed. The convention
for naive market retrieval timestamps remains limited to the market adapter;
canonical/raw accounting timestamps must remain aware. Track A is unchanged.
No operator databases/providers were accessed, and no ingestion, operator
materialization or model execution occurred. Lifecycle writes were confined to
synthetic temporary test databases.

Verification command (from backend):

```sh
TZ=Europe/London ../.venv/bin/python -m pytest tests/test_track_b_history.py tests/test_track_b_panel.py tests/test_investment_research.py tests/test_financial_strength.py tests/test_liquidity_materialization.py -q
```

The complete original 66-test set is included; parameterizing both affected
tests adds two cases, for 68. The unchanged UTC baseline passed all 66 tests.
The repaired focused tests passed all four UTC/London cases. The final complete
set passed **68 tests in 112.52 seconds**, with `TZ=Europe/London` and both
explicit session timezone cases enabled. Test-source integrity checks also
passed (2 tests), and `git diff --check` passed. Windows operator execution is
not claimed; the existing PowerShell verification block remains the operator path.
