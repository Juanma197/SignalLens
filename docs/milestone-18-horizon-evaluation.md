# Milestone 18 runbook — multi-horizon evaluation

## Frozen protocol

Before any evaluation, the code fixes the family at **21, 63, 126 and 252
trading sessions**. All use the last observed session in each calendar month,
the same current eligible catalogue, and the existing scoring weights. There is
no result-dependent horizon or threshold selection.

Features end at the vintage session. Labels start after that boundary and end
exactly the locked number of global trading sessions later. A missing endpoint,
incomplete final window, future retrieval, or segment boundary in either the
126-session feature window or label window is withheld and accounted for.
Model-ready construction retains its historical point-in-time FX as-of rules.

Long labels overlap monthly vintages. Therefore inference uses a deterministic
circular moving-block bootstrap on the ordered, equal-weight vintage excess
returns. Block length is `ceil(horizon_sessions / 21)`: 1, 3, 6 and 12 monthly
vintages respectively. The two-sided 95% percentile interval and one-sided,
null-centred raw p-value use 4,000 resamples and a fixed seed. The complete
four-horizon family then receives a predeclared Holm-Bonferroni correction at
family-wise alpha 0.05. Missing p-values fail closed.

## Run

Use explicit, different database paths and a timezone-aware cutoff:

```bash
cd backend
PYTHONPATH=. python -m app.eodhd_ingestion_cli research-horizon-evaluation \
  --research-db /absolute/path/research.duckdb \
  --production-db /absolute/path/production.duckdb \
  --decision-at 2026-09-28T20:00:00+00:00 > milestone-18.json
```

No API token is needed. Do not invoke fundamentals, ingestion, Railway or a
publisher. The command opens the research database with DuckDB `read_only=True`
and only fingerprints production. A changed fingerprint raises an error.

## Review

For every horizon inspect `evaluation`, `dependence_adjusted_inference`,
`multiple_testing`, and `horizon_gates`. A pass requires sample size, positive
baseline, confidence interval above zero, rank discrimination, temporal and all-
region stability, label/integrity controls, and Holm-adjusted significance.
Weak evidence everywhere, or one marginal raw result, is a failure.

The output always contains `candidates: []`, `ranking_generated: false`, and
`candidate_generation_authorized: false` for every horizon. Even a complete pass
is only selection evidence: retain the artifact for human review and create a
separate milestone to lock a horizon before any candidate work.

The embedded frozen 21-session baseline is: 41,271 predictions, 113 vintages,
mean excess return -0.00463, positive-period rate 0.4779, rank correlation
0.04284, and `failed_ranking_withheld`. Never replace it with this run.
