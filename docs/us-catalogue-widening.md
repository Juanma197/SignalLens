# Widening the US catalogue (about 2,000 companies)

The research database holds 100 US listings chosen by a hash sample. This runbook
replaces that sample with every US listing whose estimated market cap is near the
research band ($300M–$10B, widened by 25% either side), up to 2,500. Everything
runs in Windows PowerShell 5.1 from the repository root, one stage at a time.
Every stage is resumable: if one stops (budget, runtime or network), rerun the same
command and it continues where it left off.

The frozen Track A experiment uses "current-catalogue US common stocks", so its
universe widens too (decided 2026-10-09, before its first monthly vintage).

Needs: PRs #117 and #118 and the pre-screen PR merged, the prototype stopped, about
10 GB free disk, your EODHD token and an SEC contact header (SEC asks for a name and
email in every request's User-Agent).

| Stage | Requests | Rough time |
|---|---|---|
| 1 Pre-screen | 4 SEC + 1 EODHD | 1 min |
| 2 Catalogue | 5 EODHD | 1 min |
| 3 Prices and dividends | ~2 per new company (~4,000 EODHD) | 1–2 h at 60/min |
| 4 SEC fundamentals | 2 per company (~4,000 SEC) | 30–60 min |
| 5 SEC industry and share counts | 2 per company (~4,000 SEC) | 30–60 min |
| 6 SEC filing events (optional) | 1 per company | 15–30 min |
| 7 Classification | none | 5–15 min |

## 0. Setup and backup

```powershell
$R = "backend\data\research\signallens-research.duckdb"
$P = "backend\data\signallens.duckdb"
$Py = ".venv\Scripts\python.exe"
Copy-Item $R "backend\data\research\backups\signallens-research-pre-widening.duckdb"
$Secure = Read-Host "EODHD API token (hidden)" -AsSecureString
$env:SIGNALLENS_EODHD_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research <your name> <your email>"
Set-Location backend; $R = "data\research\signallens-research.duckdb"; $P = "data\signallens.duckdb"; $Py = "..\.venv\Scripts\python.exe"
```

## 1. Market-cap pre-screen

```powershell
& $Py -m app.us_size_prescreen --research-db $R --production-db $P
```

Check `by_size`: expect roughly 1,500–2,500 in `300m_to_10b`. Companies whose
cover page lists only share classes have no estimate and are excluded (reported
by the next stage as `excluded_size_unknown`).

## 2. Catalogue (dry run first)

```powershell
& $Py -m app.eodhd_ingestion_cli dry-run --research-db $R --production-db $P --us-securities 2500
& $Py -m app.eodhd_ingestion_cli ingest-catalogue --research-db $R --production-db $P --us-securities 2500
```

The dry run changes nothing. Check `selected_by_region.US` and
`exclusions_by_reason` before running the second command. Other regions keep their
100 each.

## 3. Prices and dividends (ten years for new companies)

```powershell
& $Py -m app.eodhd_ingestion_cli ingest-prices --research-db $R --production-db $P --daily-request-budget 6000 --requests-per-minute 60 --maximum-runtime-seconds 14400
```

If the status is `partial_checkpointed`, continue with:

```powershell
& $Py -m app.eodhd_ingestion_cli resume --research-db $R --production-db $P --daily-request-budget 6000 --requests-per-minute 60 --maximum-runtime-seconds 14400
```

## 4. SEC fundamentals

```powershell
& $Py -m app.sec_ingestion_cli ingest-sec-fundamentals --research-db $R --production-db $P --authorization "I AUTHORIZE RESEARCH-ONLY SEC INGESTION" --max-requests 6000 --runtime-seconds 14400 --max-response-bytes 10000000
& $Py -m app.sec_ingestion_cli retry-sec-failures --research-db $R --production-db $P --authorization "I AUTHORIZE RESEARCH-ONLY SEC INGESTION" --max-requests 6000 --runtime-seconds 14400 --max-response-bytes 10000000
```

Rerun the first command if it reports `stopped`; completed companies are skipped.

## 5. SEC industry codes and share counts

```powershell
$Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
& $Py -m app.sec_ingestion_cli plan-sec-liquidity-evidence-ingestion --research-db $R --production-db $P --decision-at $Now --max-request-budget 6000
```

Copy `plan_identifier` from the output (the plan expires; apply soon after), then:

```powershell
& $Py -m app.sec_ingestion_cli apply-sec-liquidity-evidence-ingestion --research-db $R --production-db $P --decision-at $Now --plan-identifier "<plan_identifier>" --max-request-budget 6000 --authorization "I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION"
```

## 6. SEC filing events (optional; feeds the risk flags)

```powershell
& $Py -m app.sec_events_cli ingest-sec-events --research-db $R --production-db $P --authorization "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION" --max-requests 6000 --runtime-seconds 14400
```

## 7. Classification

```powershell
$Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
& $Py -m app.investment_research_cli materialize-stored-investment-evidence --research-db $R --production-db $P --decision-at $Now --authorization "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"
```

## 8. Check and clean up

```powershell
Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN; Remove-Variable Secure
Set-Location ..
.\scripts\monthly-prototype.ps1 -SkipRefresh
```

The roster line shows the new eligible count. Then start the prototype and open
**This month** with the current time as the cutoff.

## Afterwards

- The monthly refresh now covers about 2,500 US listings plus 400 elsewhere, roughly
  5,000 EODHD requests. Run it with
  `.\scripts\monthly-prototype.ps1 -DailyRequestBudget 6000 -RequestsPerMinute 60`.
- The first assessment after widening reads a larger database (expect tens of
  seconds); later page loads reuse it.
- To undo: stop everything and copy the backup from step 0 over the research
  database.
