# Monthly prototype routine

Once a month, in Windows PowerShell 5.1 from the repository root. Design details
and evidence rules are in [prototype-first-slice.md](prototype-first-slice.md).

## 1. Refresh prices and review the roster

Stop the prototype first (press Enter in its window), then:

```powershell
.\scripts\monthly-prototype.ps1
```

It stops at the first failed stage and tells you what to do:

1. Preflight: databases present, prototype stopped, no other ingestion running.
2. Backup of the research database, verified by SHA-256
   (`backend\data\research\backups\signallens-research-pre-refresh-<time>.duckdb`).
3. Refresh dry run (no requests, no writes).
4. Price refresh from EODHD (asks for your token, hidden; about 1,000 requests,
   roughly 50 minutes). If it stops at the request budget, rerun tomorrow.
5. Read-only roster at the current time, saved to
   `backend\data\research\reports\prototype-roster-<time>.json`.

To review the roster without refreshing: `.\scripts\monthly-prototype.ps1 -SkipRefresh`.

## 2. Review and record the snapshot

```powershell
.\scripts\start-prototype.ps1 -Operator
```

- Open the printed URL and enter the cutoff printed by step 5 (or the time shown).
- Review the shortlist, withheld companies and company pages.
- On **Snapshots**, record this month's snapshot with the same cutoff. One per
  month; it must be within 14 days of the cutoff and cannot be changed afterwards.

## 3. Research

- Add interesting companies to the **Watchlist**.
- Write or revise each company's **thesis**; every save keeps earlier versions.
- Open earlier snapshots to see results at 21/63/126/252 trading sessions.

## 4. Update your portfolio

On **Portfolio**, record every buy and sell you made with your broker since last
month (ticker, shares, price, fees, date and, ideally, why). SignalLens never
places orders. Holdings use the average-cost method and are valued at the latest
stored close; a ticker outside SignalLens data shows its cost but no value. Trades
cannot be edited or deleted: void a mistake and record the correct trade.

## 5. Check every thesis

On each held or watched company page, add **Thesis checks**: conditions such as
"operating margin at least 10%" or "liabilities / assets at most 60%". Then open
**Thesis checks** with this month's cutoff. Broken theses come first, including
automatic value-trap signs (losses, negative free cash flow, falling revenue,
negative equity) even where you set no conditions. Missing figures show as
unknown, never as a pass.

## 6. Read this month's decisions

Open **This month** with the cutoff. It lists up to three new undervalued
opportunities (marked if already held) and a decision for every holding:
SELL (thesis broken, or price 20%+ above the middle-case value), REDUCE (price
above it, or a position over 35%), REVIEW (no SignalLens evidence), BUY MORE
(15%+ upside, qualifying ranking, no warnings, position under 25%) or HOLD.
Every decision lists its reasons. Nothing is executed: you place any trade with
your broker, then record it on **Portfolio**.

Enter any **new money to invest** to get a suggested allocation: sales first
(SELL sells all; REDUCE sells half when overvalued, or trims to 25% when too
large), then money split by ranking score between BUY MORE holdings and new
picks, with no position above 25% and whole shares only. Whatever does not fit
stays as cash.

## Things to know

- Recorded snapshots are safe from later refreshes. Recomputing an old cutoff
  after a refresh can differ, because the refresh rewrites the last 7 days of prices.
- Results are an unvalidated momentum baseline with zero validation credit.
- Press Enter to stop the prototype; closing the window leaves it running.
