# Holding alerts on your phone (Telegram)

Every weekday before the US market opens (12:00 UTC), SignalLens checks your
holdings against the previous close and overnight filings, and messages
you **only when a decision changes**: for example `HOLD → SELL` because a new
quarterly report shows a loss, or `HOLD → BUY MORE` because the price fell while the
thesis still holds. On Fridays it also sends a short summary. The first run sends one
"alerts are on" message listing every current decision.

Each change message says what changed, how much it means in shares and dollars, and
why:

```
EGY.US: HOLD → SELL
Sell all 200 shares (≈ $1,200).
• The thesis is broken:
• Operating loss in the latest fiscal year.
Upside -36% · weight 23% · thesis broken
```

Decision support only: nothing is ever traded, and the rules are not yet validated
against outcomes (see the Scorecard).

## What a run does

1. Prices and dividends: EODHD's whole-market file for each trading day since the
   last update (two requests per day; `app.daily_prices`). A day is never skipped:
   if more than a month is missing, it asks for the full monthly refresh instead.
2. Filings: for each holding, SEC's list of recent filings (one request each). When
   a new 10-K or 10-Q is listed, that company's figures are re-read.
3. Decisions: the same rules as the **This month** page, at the current time.
4. Messages: only for holdings whose decision or thesis status differs from the last
   message sent. A message that fails to send is retried on the next run.

## One-time Telegram setup (about 2 minutes)

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name. It replies with a
   **bot token** like `123456:ABC...`. Treat it like a password.
2. Open your new bot and send it any message (for example "hi").
3. In PowerShell, from `backend`:

```powershell
$env:SIGNALLENS_TELEGRAM_BOT_TOKEN = "<bot token>"
..\.venv\Scripts\python.exe -m app.prototype.alerts --telegram-chat-id
```

   It prints your chat id. Keep both values; on Railway they become two secrets.

## Try it locally

With the prototype stopped (the alert run writes to the databases):

```powershell
$env:SIGNALLENS_TELEGRAM_BOT_TOKEN = "<bot token>"
$env:SIGNALLENS_TELEGRAM_CHAT_ID = "<chat id>"
..\.venv\Scripts\python.exe -m app.prototype.alerts --dry-run --skip-update   # prints, sends nothing
..\.venv\Scripts\python.exe -m app.prototype.alerts --skip-update              # sends the first message
```

For a full run that also downloads prices and checks filings, also set
`SIGNALLENS_EODHD_API_TOKEN` and `SIGNALLENS_SEC_USER_AGENT` and drop `--skip-update`.
`SIGNALLENS_PUBLIC_URL` (for example your Railway address) adds a link to each
message.

## Running it without your laptop

The plan is to run this on Railway as a daily job next to the website, with the four
values above stored as Railway secrets. That deployment is a separate step (the job
and the website must not write the database at the same time).
