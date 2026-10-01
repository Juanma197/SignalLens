# SEC ingestion status recovery

## Failure and scope

The post-pilot failure was confined to status reporting. DuckDB returned SQL `NULL`
as a Python `None` key from `SELECT currency, count(*) ... GROUP BY currency`.
`sec-ingestion-status` placed that result directly in `currencies`; the CLI then
called `json.dumps(..., sort_keys=True)`. A result containing both `None` and a
string such as `"USD"` made Python compare unlike key types while sorting and
raised exactly:

```text
TypeError: '<' not supported between instances of 'str' and 'NoneType'
```

The ingestion write path is unchanged. Status now converts aggregate keys and
DuckDB scalar values to deterministic JSON primitives, represents a null group as
`(null)`, bounds and sanitizes failure samples, and exposes explicit latest-run
checkpoint consistency checks.

## Pilot interpretation

A three-request run with one completed issuer is coherent: one request retrieves
the ticker-to-CIK mapping and two requests retrieve that issuer's submissions and
company facts. Encountering the request limit as the next issuer starts creates a
retryable checkpoint for that issuer. The status checks also verify that the run
did not exceed its budget, the recorded inserted/revision counts exist in storage,
and a request-budget stop both exhausted the budget and left a retryable checkpoint.

Thus 714 stored observations, including 337 revisions, are logically consistent
with the checkpoint shape. These checks establish internal consistency, not an
independent validation of SEC response content.

## Post-merge read-only verification

Run status first (both database files are SHA-256 fingerprinted before and after):

```bash
python -m app.sec_ingestion_cli sec-ingestion-status \
  --research-db <research> --production-db <production>
```

Then verify that the completed issuer is excluded from the resume plan:

```bash
python -m app.sec_ingestion_cli plan-sec-ingestion \
  --research-db <research> --production-db <production>
```

For the reported pilot shape, status should show one completed, one retryable, 98
pending, 714 observations, 337 revisions, no rankings or candidates, and a
`consistent` checkpoint diagnostic. The resume plan should show one completed and
99 pending because retryable work remains eligible for a normal resume.
