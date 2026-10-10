# Backtest phase 3: delisted companies

Implements phase 3 of [the pre-registration](backtest-preregistration.md). The
replay's catalogue is today's listings, so companies that failed, were taken over
or left the exchange since 2019 were missing. For a value strategy those are
exactly the cheap companies that went wrong, so **no backtest result is treated as
trustworthy before this phase**.

Everything here is research-only. The live prototype (shortlist, This month, the
daily alerts) never lists a delisted company; only the replay does, and only in the
months the company traded.

## How a delisted company is found and verified

| Step | Source | Cost |
|---|---|---|
| Delisted US common stocks on NYSE, NASDAQ, NYSE American/Arca (no OTC, ADRs, warrants, SPACs) | EODHD `exchange-symbol-list/US?delisted=1` | 1 EODHD request |
| Company name matched to SEC's list of every filer name, including former names (EDGAR's "/DE/"-style marks, punctuation and legal-form words ignored) | SEC `cik-lookup-data.txt` | 1 SEC download |
| Each candidate filer verified: US annual or quarterly reports (10-K/10-Q) since 2017. Several verified filers for one name: the one whose SEC record still lists the ticker, otherwise excluded as ambiguous | SEC submissions | 1 SEC request per candidate filer |
| Daily prices, kept only when trading ended between January 2019 and 30 days ago, and the last 10-K/10-Q came within 18 months of the last session | EODHD `eod` and `div` | 2 EODHD requests per company |
| SEC facts, industry code and share counts, with the verified filer | the existing SEC fundamentals ingestion | 2 SEC requests per company |

Excluded, with the reason recorded: tickers now used by an active catalogue
listing (EODHD's prices would be the new company's), filers already in the
catalogue (a ticker change, not a delisting), names SEC does not know, ambiguous
names, foreign filers (20-F/40-F), filers without recent 10-K/10-Q, companies still
trading, and filings that do not match the trading period.

In the replay a delisted company joins the catalogue for the months between its
first and last stored session. In the measurement a pick or holding that stops
trading is sold at its last price (then cash) instead of being dropped, which would
bring survivorship back.

## Commands (PowerShell, from `backend`)

```powershell
$R = "data\research\signallens-research.duckdb"; $P = "data\signallens.duckdb"; $Py = "..\.venv\Scripts\python.exe"
$Secure = Read-Host "EODHD API token (hidden)" -AsSecureString
$env:SIGNALLENS_EODHD_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research <your name> <your email>"

# 1. Discover and verify (1 EODHD request; SEC only). Reports how many EODHD requests step 2 needs.
& $Py -m app.delisted discover --research-db $R --production-db $P
#    If it stopped at the SEC request budget, continue without re-reading the lists:
& $Py -m app.delisted discover --research-db $R --production-db $P --verify-only

# 2. Prices (2 EODHD requests per company; resumable: rerun until nothing is left)
& $Py -m app.delisted prices --research-db $R --production-db $P

# 3. SEC facts for the priced companies
& $Py -m app.delisted sec --research-db $R --production-db $P

# 4. Classification and filing events now include them (the usual stages 5 and 6)
$Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
& $Py -m app.investment_research_cli materialize-stored-investment-evidence --research-db $R --production-db $P --decision-at $Now --authorization "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"
& $Py -m app.sec_events_cli ingest-sec-events --research-db $R --production-db $P --authorization "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION" --max-requests 6000 --runtime-seconds 14400

# 5. Replay the tuning window again and measure it
& $Py -m app.prototype.replay run --replay-db data\research\backtest\replay.duckdb --note "phase 3: with delisted companies"
& $Py -m app.prototype.measure --replay-db data\research\backtest\replay.duckdb

# Progress at any time
& $Py -m app.delisted status --research-db $R --production-db $P
Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN
```

The measurement reports `delisted_company_months` and no longer lists "survivors
only" among the reasons the run is not decisive. The tuning window remains not
decisive: only the one-time holdout (2023 onwards) can be.

## Known limits

- Name matching misses companies whose EODHD and SEC names differ beyond legal-form
  words; `status` reports how many were not found. A coverage note goes with every
  result: the share of delisted candidates that could be verified.
- Today's catalogue was chosen by today's size: companies that later grew out of the
  band or shrank below it and still trade are also missing. Phase 3 does not fix
  that; it adds the companies that stopped trading.
- A delisted holding is valued at its last traded price. Cash paid in a takeover
  after the last session, or a later bankruptcy recovery, is not counted.
