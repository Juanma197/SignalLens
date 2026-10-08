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

## Things to know

- Recorded snapshots are safe from later refreshes. Recomputing an old cutoff
  after a refresh can differ, because the refresh rewrites the last 7 days of prices.
- Results are an unvalidated momentum baseline with zero validation credit.
- Press Enter to stop the prototype; closing the window leaves it running.
