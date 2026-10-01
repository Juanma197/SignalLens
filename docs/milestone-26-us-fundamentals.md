# Milestone 26 — US point-in-time fundamentals runbook

## Pre-registration and scope

This is a **US-only, read-only research comparison** of the unchanged price-only model with a new price-plus-fundamentals hypothesis. The 126- and 252-session horizons were frozen before evaluation. The current catalogue is current-membership and is **not survivorship-free**. LSE, TO, XETRA and PA remain on the unchanged price-only baseline; they are never assigned missing or poor SEC factors. Comparable point-in-time sources are required before international fundamentals can be studied.

Successful offline synthetic tests establish mechanics, not live evidence. No recommendation can be produced unless every locked coverage, integrity, improvement, confidence, dependence and multiple-testing gate passes. This command never produces rankings, candidates, Top 3 output, publication, database writes, or investment advice.

## Locked fact policy

At a decision timestamp, a fact is visible only when `public_at <= decision_at`. Missing availability is excluded; fiscal-period end is never substituted. For the same security, taxonomy, concept, unit, currency, start and end, the latest visible `public_at`/accession wins, so amendments appear only after publication. Provenance (taxonomy, concept, unit, currency, accession, source) remains on selected facts. Nonfinite values and incompatible units/currencies are rejected.

Flows use the latest 300–400 day annual observation, or exactly four 70–110 day, non-overlapping discrete quarters. YTD facts are identified by duration but never summed with quarters, preventing overlap. Flow staleness is 550 days; instant staleness is 460 days. Missing values are never converted to zero.

## Frozen formulas

All income/cash-flow numerators below are TTM/annual. Assets, equity and debt are the latest visible instant. `ε = 1e-12`; denominators at or below safeguards are withheld.

| Family / factor | Formula and safeguards | Desirable | Minimum history |
|---|---|---:|---|
| Growth / revenue growth | `(revenue_ttm - revenue_prior_ttm) / abs(revenue_prior_ttm)`; prior revenue positive | Higher | Two comparable years |
| Growth / EPS growth | `(diluted_EPS_ttm - prior) / abs(prior)`; current EPS nonnegative and prior positive | Higher | Two comparable years |
| Profitability / operating margin | `operating_income_ttm / revenue_ttm`; revenue positive | Higher | One TTM |
| Profitability / net margin | `net_income_ttm / revenue_ttm`; revenue positive; loss retained | Higher | One TTM |
| Profitability / ROA | `net_income_ttm / assets`; assets positive | Higher | TTM + instant |
| Profitability / ROE | `net_income_ttm / equity`; nonpositive equity withheld | Higher | TTM + instant |
| Cash flow / trailing FCF | `operating_cash_flow_ttm - abs(capex_ttm)`; negative FCF retained | Higher | One TTM |
| Cash flow / FCF margin | `FCF / revenue_ttm`; revenue positive | Higher | One TTM |
| Cash flow / OCF growth | `(OCF_ttm - prior_OCF_ttm) / abs(prior)`; prior positive | Higher | Two years |
| Leverage / debt to assets | `debt / assets`; assets positive | Lower | Latest instant |
| Leverage / debt to equity | `debt / equity`; nonpositive equity withheld | Lower | Latest instant |
| Leverage / FCF-to-debt | `FCF / debt`; debt positive; negative FCF retained | Higher | TTM + instant |
| Dilution / diluted-share growth | `(diluted_shares_ttm - prior) / abs(prior)`; prior positive | **Lower** | Two years |
| Valuation / earnings yield | `net_income_ttm / (historical_price × point-in-time shares)`; market cap positive; losses retained | Higher | TTM + price/shares |
| Valuation / book-to-market | `positive_equity / historical_market_cap`; nonpositive equity withheld | Higher | Instant + price/shares |
| Valuation / FCF yield | `FCF / historical_market_cap`; negative FCF retained | Higher | TTM + price/shares |

Interest coverage remains unavailable because defensible interest-expense evidence was absent. Valuation uses the historical adjusted price visible at the decision and a visible share measure—never present-day market capitalisation. Inadequate share, currency, or denominator integrity causes withholding.

## Normalization, fairness and locked weights

Each factor is tie-neutral percentile-ranked within the US vintage after requiring at least five valid companies. A security needs at least three represented families; otherwise it is withheld, not assigned a neutral, zero, or poor score. Family scores average only valid factors. Locked weights are growth 20%, profitability 25%, cash flow 20%, leverage 15%, dilution 5%, valuation 15%. The enhanced score is 60% unchanged price-only percentile plus 40% fundamental composite.

Coverage is reported for every factor/vintage. The 11 permanently unmapped issuers are a separate count and bounded symbol sample, never financially weak observations. The versioned configuration hash covers concepts, directions, horizons, safeguards, weights and policy.

## Evidence and gates

Both hypotheses use identical US vintages, eligible securities, price segments, labels and transaction-free assumptions, and compare only a matched factor-eligible panel. Output includes counts, coverage, withholding, both return records, incremental excess, rank correlation, positive-period rate, temporal confidence, concentration, moving-block dependence inference, Holm-adjusted significance and integrity failures. A positive enhanced return is insufficient: incremental improvement over matched price-only must be positive with a positive confidence interval and adjusted significance at 5%.

## Post-merge operator command (PowerShell)

```powershell
Push-Location backend; $ResearchDb = "C:\SignalLens\data\research.duckdb"; $ProductionDb = "C:\SignalLens\data\production.duckdb"; & ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli research-us-fundamentals-evaluation --research-db $ResearchDb --production-db $ProductionDb --decision-at "2026-10-01T00:00:00+00:00"; Pop-Location
```

Replace both example paths with the operator's real backend database paths. The command opens both files read-only, rejects missing/identical/symlinked/hard-linked paths, fingerprints both before and after, and raises if either changes. Do not run it against operator data before merge; it makes no provider or Railway request.
