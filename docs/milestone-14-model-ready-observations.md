# Milestone 14 model-ready observation contract

Milestone 14 is a **research-only, offline transformation**. It converts sanitized
frames shaped like the existing five-region pilot tables into a trustworthy
current cross-section. It does not open a DuckDB file, call EODHD or another
provider, deploy anything, write a shadow or production vintage, or publish a
ranking. Its output label is `RESEARCH ONLY — NOT INVESTMENT ADVICE`.

The roadmap had no numbered milestone after Milestone 13. This focused milestone
therefore takes the smallest next step toward the intended monthly Top 3: establish
which current observations are safe inputs before evaluating or ranking them.

## Input and point-in-time contract

`app.research_observations.build_model_ready_observations` accepts five data
frames supplied by its caller:

1. current-catalogue securities from US, LSE, TO, XETRA and PA;
2. local-currency unadjusted OHLCV plus provider-adjusted close;
3. historical USD/GBP, CAD/GBP and EUR/GBP observations with `available_at`;
4. splits and cash distributions; and
5. bounded structured provider failures.

Only prices retrieved by `decision_at` are visible. Non-GBP conversions use the
latest FX observation on or before each price date that was available by the
decision boundary. GBP needs no conversion and GBX is divided by 100. Today's FX
is never substituted for a missing historical rate. Momentum features reuse the
existing `global_research.momentum_features` implementation and use adjusted close
as the total-return input; raw OHLCV and action validation remain independent
quality checks.

This milestone intentionally consumes current catalogue membership. The resulting
cross-section is **not survivorship-free** and cannot support historical-membership,
delisting-complete, or historical performance claims.

## Fail-closed rules

The entire build is rejected for a missing region, invalid venue currency,
duplicate security/symbol, or duplicate price, FX, action, or failure observation.
This avoids choosing among ambiguous records.

An individual security is present but ineligible, with explicit reasons, when it
has any of the following:

- a permanent provider failure;
- no visible prices or fewer than 127 sessions;
- nonfinite, missing, nonpositive or impossible OHLC/adjusted values, negative
  volume, or duplicate dates across sources;
- a stale latest price;
- missing, invalid or stale point-in-time FX;
- an unsupported, missing, nonpositive or otherwise invalid corporate action.

No missing value becomes zero and no ineligible security receives features. The
aggregate report distinguishes eligible and withheld observations by region and
reason. `top_three_available` says only that at least three observations survived;
it is not a recommendation. `highest_conviction_available` remains false because
this milestone neither scores nor evaluates candidates.

## Offline verification and next boundary

The regression fixture deterministically represents all five regions and all four
trading-currency behaviours (USD, GBP/GBX, CAD and EUR). Tests cover every safety
class above without a database, credential, provider request or local operator
asset.

A later milestone may connect this output to the existing controlled
walk-forward/multifactor research path. Before emitting even a research-only
monthly Top 3, that work must add approved point-in-time fundamentals, define
monthly decision/session boundaries, apply the existing promotion gates, and
withhold all candidates when evidence or confidence is weak. Production tables,
the production API/dashboard cards, Railway, and `momentum_126d` remain out of
scope.
