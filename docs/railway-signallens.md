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

## Infrastructure as code

The Railway services, their build and deploy settings and the volume are defined in
`.railway/railway.ts` (it replaced `backend/railway.toml` and `frontend/railway.toml`,
whose Config-as-Code format Railway retires on 2026-12-01). Secrets appear only as
`preserve()`; their values stay in the dashboard. To change infrastructure, edit the
file, then from the repository root:

```powershell
.\scripts\railway-config.ps1 plan    # read-only preview
.\scripts\railway-config.ps1 apply   # asks before changing Railway
```

`apply` manages the whole project: a resource removed from the file is deleted, so
keep every service and the volume in it.

## 1. Grow the disk and check the memory

The widened research database is a few GB, and the safe upload procedure keeps the
upload, the live copy, a rollback copy and a temporary copy at once. In the Railway
dashboard: service `SignalLens` → Volume → resize to about **4–5× the local research
database size** (check it with `Get-Item backend\data\research\signallens-research.duckdb`).
Larger volumes may need a paid Railway plan; check the price shown there.

Measured on 2026-10-10 with the widened catalogue (2.03 GB database, 1,972 companies,
1,207 eligible): **volume about 10 GB**. One assessment (the shortlist, This month,
or the daily alert run) takes about 70 s on a laptop and peaks at about **1.6 GB of
memory**, and the backend keeps up to two reports cached (each a few hundred MB). Give
the `SignalLens` service at least **3 GB of memory** (Settings → Resources, if the plan
limits it). The first page load of a date waits for a full assessment; the website
allows four minutes for it.

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

1. Research database: with the prototype stopped, run

   ```powershell
   .\scripts\sync-research-to-railway.ps1
   ```

   It does the `docs/milestone-33-railway-research-sync.md` procedure end to end:
   a verified snapshot, a capacity check against the volume (it says what to resize
   to), the upload to `/data/bootstrap/` over `scp` (using the Railway SSH key in
   `~/.ssh/railway_signallens_ed25519`), the read-only remote plan, your `yes`, the
   atomic apply (the previous database is kept as a rollback copy), the status
   checks, and removal of the upload. If the upload finished but a later step
   stopped, rerun with `-SkipUpload`.
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
