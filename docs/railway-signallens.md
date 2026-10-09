# SignalLens on Railway: website, data and daily alerts

Goal: open SignalLens from your phone, record trades there, and get Telegram
messages when a holding's decision changes, with the laptop off.

Existing project `respectful-exploration` (environment `production`):

| Service | Role |
|---|---|
| `SignalLens` | backend (FastAPI), volume `/data` |
| `worthy-patience` | website, https://worthy-patience-production-2030.up.railway.app |
| `signallens-monthly-scheduler` | older curl cron; not used by SignalLens alerts |

The backend runs in staging mode: API writes are refused except to the prototype
store (portfolio, theses, snapshots). The daily alert job writes the research
database itself, inside the backend process, under the maintenance lock.

## 1. Grow the disk

The widened research database is a few GB, and the safe upload procedure keeps the
upload, the live copy, a rollback copy and a temporary copy at once. In the Railway
dashboard: service `SignalLens` → Volume → resize to about **4–5× the local research
database size** (check it with `Get-Item backend\data\research\signallens-research.duckdb`).
Larger volumes may need a paid Railway plan; check the price shown there.

## 2. Backend variables

Non-secret (can be set from the CLI):

```powershell
railway variables --service SignalLens --set "SIGNALLENS_PROTOTYPE_DATABASE_PATH=/data/prototype/signallens-prototype.duckdb" --set "SIGNALLENS_PROTOTYPE_WRITES_ENABLED=true" --set "SIGNALLENS_ALERTS_ENABLED=true" --set "SIGNALLENS_ALERTS_UTC_TIME=12:00" --set "SIGNALLENS_PUBLIC_URL=https://worthy-patience-production-2030.up.railway.app"
```

Secrets: set them in the dashboard (service `SignalLens` → Variables) so they never
appear in a terminal history: `SIGNALLENS_TELEGRAM_BOT_TOKEN`,
`SIGNALLENS_TELEGRAM_CHAT_ID`, `SIGNALLENS_EODHD_API_TOKEN`. `SIGNALLENS_SEC_USER_AGENT`
already exists; make sure it is your contact line.

## 3. Deploy the code

Both services build from `Juanma197/SignalLens`. Merging to `main` deploys them
(or use Deploy → Redeploy in the dashboard). Check
`https://<backend>/api/v1/health` and that the website loads.

## 4. Upload the databases

After the widening has finished locally and the prototype is stopped:

1. Research database: follow `docs/milestone-33-railway-research-sync.md` (managed
   backup → upload to `/data/bootstrap/` → `plan-research-sync` → `apply-research-sync`
   → verify). For the upload, `railway ssh config --service SignalLens` adds an
   OpenSSH host so that `scp` can copy the file to `/data/bootstrap/`.
2. Prototype store (your trades, theses, snapshots): copy
   `backend\data\prototype\signallens-prototype.duckdb` to
   `/data/prototype/signallens-prototype.duckdb` with the backend stopped or before
   enabling alerts. If you have not recorded trades yet, skip this and enter them on
   the Railway website instead.

## 5. Check the alerts

- `GET /api/v1/research/prototype/alerts/status` (through the website's API proxy:
  `/api/research/prototype/alerts/status`) shows whether alerts are enabled, Telegram
  is configured, the next run and the last result.
- The first run sends one "SignalLens alerts are on" message listing your holdings.
- Afterwards: a message only when a holding's decision or thesis status changes,
  and a short Friday summary.

The daily run is at 12:00 UTC on weekdays: before the US open (13:30 UTC in
summer, 14:30 in winter), so a message can be acted on the same day. It uses the
previous close and filings published overnight. During the few minutes it runs the
website shows "temporarily unavailable" for research pages; reload after a minute.

## Monthly

The daily job keeps prices current (two requests per trading day). The full monthly
refresh, new filings for companies you do not own, and recording the month still run
from the laptop; then sync the research database again (step 4.1).
