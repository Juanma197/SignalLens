# Extending the prototype beyond US companies: options

Written 2026-10-08. A decision document, not an implementation.

## Where things stand

- **Prices are already global.** The research database holds 10 years of daily
  prices for about 100 securities each on LSE, Toronto (TO), XETRA and Paris (PA),
  plus USD/CAD/EUR-to-GBP exchange rates, from your EODHD "EOD All World" plan.
- **Everything else in the prototype is US-only.** Identity (SEC CIK), industry
  (SEC SIC code), size (SEC cover-page share count) and the financial-health brief
  (SEC 10-K facts) all come from SEC filings, which exist only for US-listed
  companies. A non-US company today fails identity and never reaches the shortlist.

To include a non-US company the prototype needs, per company: a durable identity
(ISIN), an industry code, a share count, annual financial statements in the
reporting currency, and FX to compare sizes in one currency. FX is already stored.

## Options

| | A. EODHD Fundamentals | B. Free filings (ESEF/UKSEF) | C. Hybrid |
|---|---|---|---|
| What | Add EODHD's fundamentals plan: statements, shares, sector, market cap for most exchanges | Annual reports in inline XBRL from filings.xbrl.org (EU/UK listed companies) | A for UK/EU/Canada now; keep SEC for US |
| Cost | About 60 (USD or EUR; third-party listings disagree) per month on top of your current plan. Check EODHD's pricing page | Free | As A |
| Coverage | UK, EU, Canada and more | EU and UK only, admittedly incomplete; Canada (SEDAR+) has no practical API | Broad |
| Data quality | Provider-normalised, not the filing itself; no reliable publication timestamps, so only prospective (forward-looking) use, which suits monthly snapshots | Primary source, IFRS taxonomy; annual only; each company's tagging differs | US primary, others normalised |
| Work | About 2 milestones: a separate fundamentals table, then non-US identity, size and brief | About 3-4 milestones: discovery, download, IFRS concept mapping, validation | About 2 milestones |

## Things that change outside the US

- **Accounting:** IFRS rather than US GAAP (for example, leases are on the balance
  sheet under IFRS 16, which raises reported debt and operating cash flow). The
  financial-health rules would need region notes, not the US thresholds unchanged.
- **Currency:** size bands and comparisons need one currency (GBP or USD) via the
  stored FX rates, with the rate date shown.
- **Calendars:** each exchange has its own trading days; the session calendar
  would be derived per exchange, as it is for the US today.
- **Universe:** the stored catalogue holds about 100 names per region, chosen by an
  earlier deterministic shortlist, not a full market. Finding small, overlooked
  companies would need a wider catalogue per region (prices are cheap on your plan).

## Recommendation

1. Finish the US workflow first (Stage 3 valuation is next), because every later
   region reuses it.
2. If diversification matters to you now, take **option C**: one month of EODHD
   Fundamentals, limited to UK, XETRA, Paris and Toronto small/mid caps, as a trial.
   Cancel if the data is not usable. Buying it is your decision and your action.
3. Keep option B in reserve if you want primary-source data for UK/EU later.

Sources: [EODHD plan listing (apis.io)](https://apis.io/plans/eodhd/eodhd-plans-pricing/),
[EODHD pricing on G2](https://www.g2.com/products/eodhd-financial-data-apis/pricing),
[XBRL International on filings.xbrl.org](https://www.xbrl.org/news/well-over-3000-esef-filings-at-filings-xbrl-org-where-are-they-coming-from-and-how-can-we-improve-access/),
[xbrl-filings-api (PyPI)](https://pypi.org/project/xbrl-filings-api).
