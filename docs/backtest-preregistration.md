# Backtest pre-registration: the prototype's monthly rules

Written on 2026-10-09, **before any backtest code or result exists**. Its job is
to fix the question, the comparison and the pass criteria in advance, so results
cannot be reinterpreted after they are seen. Changes to this file after the first
run must be listed under *Amendments* with a date and a reason.

## Question

Would the prototype's monthly rules (Top 3 undervaluation ranking with the
value-trap filter, BUY MORE / HOLD / REDUCE / SELL decisions, and the cash-pool
allocation) have grown a £200-a-month portfolio by more than putting the same
money into a global index fund like VALL, after costs and in pounds?

A working alert system is not evidence that the strategy works; this test is.

## What is known before starting

1. **The live as-of rule cannot look back.** The prototype uses only data
   SignalLens had *retrieved* by the cutoff. Almost everything was retrieved in
   2026, so any earlier cutoff sees nothing. The backtest uses a separate replay
   mode in which data counts from its *public* date: SEC figures from the day
   after filing (as originally filed, so later restatements cannot leak back),
   prices from the day after the trading session. The live rule is unchanged.
2. **The universe is survivors.** The catalogue lists companies alive today,
   chosen by today's market cap. Companies that failed or were delisted since 2019,
   and small ones that later grew into the size band, are missing. This flatters a
   value strategy, whose losers are exactly the cheap companies that died.
3. **History depth.** The valuation scenarios need five fiscal years of accounts
   and the company's own valuation history; prices start in 2016. The first
   decision month is **January 2019**: about 93 monthly decisions to September 2026.
4. **To verify before trusting market caps:** whether stored EODHD closes are
   adjusted for later stock splits. If they are, SEC share count x close is wrong
   around splits and must be corrected with split history.

## Method

### Phase 1: replay
- Monthly cutoffs (the first trading day of each month), January 2019 to
  September 2026, using public dates as above.
- Size measured at each cutoff from SEC shares outstanding x price on that date.
- Every month's full output is saved with the rules version and configuration
  hash. The rules are frozen for the run.

### Phase 2: measurement (pounds, total return including dividends)
- **Picks:** forward 1-, 3-, 6- and 12-month returns of the Top 3 against
  (a) 1,000 random sets of three from the same month's eligible companies,
  (b) all eligible companies equally weighted, (c) a VALL-like global index,
  (d) the S&P 500 (reported, not decisive). Confidence intervals by bootstrapping
  months.
- **Decisions:** later returns of SELL, REDUCE and BUY MORE holdings against
  HOLD and against the eligible universe; later returns of companies the
  value-trap filter excluded.
- **Portfolio simulation of the actual policy:** £200 deposited monthly, the cash
  pool, at most 10 holdings, the 25% position limit, fractional shares, and
  Trading 212 costs (0.15% currency conversion each way plus an estimated spread).
  Compared with the same deposits into the VALL-like index. Reported: money-
  weighted return, annualised return, maximum drawdown, volatility, turnover,
  average cash held.

### Phase 3: delisted companies
- Add delisted US companies (EODHD delisted listings and their prices; SEC
  filings exist for them) and rerun phases 1 and 2. **No result is treated as
  trustworthy before phase 3.**

## Discipline

- **Tuning window:** rules may be adjusted only using 2019-01 to 2022-12.
- **Holdout:** 2023-01 to 2026-09 is not looked at until the rules are final, then
  run once. Its result stands, whatever it is.
- Every run is recorded (date, rules version, configuration hash, phase), including
  runs that look bad.
- Research confidence (how sure the analysis is) is reported separately from the
  observed probability that a pick made money.

## Pass criteria (holdout, after phase 3)

The strategy is judged to add value only if **both** hold over 2023-01 to 2026-09:

1. **Portfolio:** the simulated SignalLens portfolio ends with more money than the
   same deposits into the VALL-like index, after costs, in pounds.
2. **Skill:** the Top 3's average 12-month return beats the random-pick baseline,
   with the 90% bootstrap interval of the difference above zero.

If (1) holds but (2) does not, the result is luck or universe effects, not
evidence of skill. If neither holds, the honest conclusion is that the rules do
not beat the index fund. With about 45 holdout months, **"inconclusive" is a likely
and legitimate outcome**; it means "not yet shown", and the prospective scorecard
keeps collecting evidence.

## Amendments

None yet.
