"use client";

/** "Latest data" or "as of a past date": what the pages show, without typing timestamps. */
export type When = {mode: "latest"} | {mode: "date"; date: string};

export const todayUtc = () => new Date().toISOString().slice(0, 10);
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

/** A link's exact time keeps its day; no value means the latest data. */
export function whenFromQuery(value: string | null): When {
  if (!value || !Number.isFinite(new Date(value).getTime())) return {mode: "latest"};
  const date = new Date(value).toISOString().slice(0, 10);
  return date >= todayUtc() ? {mode: "latest"} : {mode: "date", date};
}

/** The cutoff sent to the server. A past date means after that day's US close (end of day UTC). */
export function cutoffFor(when: When): string {
  if (when.mode === "latest" || when.date >= todayUtc()) return nowIso();
  return `${when.date}T23:59:59Z`;
}

export function describeCutoff(iso: string): string {
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"}) + " UTC";
}

export function WhenPicker({when, onChange, disabled}: {when: When; onChange: (when: When) => void; disabled?: boolean}) {
  const past = when.mode === "date" ? when.date : "";
  return <fieldset className="prototype-when" disabled={disabled}>
    <legend>Show data as of</legend>
    <label className="prototype-inline-check"><input type="radio" name="when" checked={when.mode === "latest"} onChange={() => onChange({mode: "latest"})}/> Latest</label>
    <label className="prototype-inline-check"><input type="radio" name="when" checked={when.mode === "date"}
      onChange={() => onChange({mode: "date", date: past || new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10)})}/> A past date</label>
    {when.mode === "date" && <input type="date" aria-label="Past date" max={todayUtc()} value={past} onChange={e => e.target.value && onChange({mode: "date", date: e.target.value})}/>}
    <small>{when.mode === "latest" ? "Uses the most recent prices and filings." : "Shows only what SignalLens could have known by the end of that day."}</small>
  </fieldset>;
}

const MESSAGES: Record<string, string> = {
  research_maintenance: "The data is being updated right now. Try again in a minute.",
  PROTOTYPE_SERVICE_UNAVAILABLE: "SignalLens could not be reached. Check that it is running, then try again.",
  PROTOTYPE_FUTURE_CUTOFF: "That date is in the future. Pick today or an earlier date.",
  PROTOTYPE_EVIDENCE_READ_FAILED: "The stored data could not be read. Try again; if it keeps failing, the data may need a refresh.",
  PROTOTYPE_DATABASE_UNAVAILABLE: "The research data is not available on this server yet.",
  PROTOTYPE_DATABASE_CHANGED: "The data changed while it was being read (an update was running). Try again.",
  PROTOTYPE_WRITES_DISABLED: "Saving is switched off on this server.",
  PROTOTYPE_SNAPSHOT_MONTH_EXISTS: "This month has already been recorded; a month can only be recorded once.",
  PROTOTYPE_RECORD_MONTH_EXISTS: "This month's decisions are already recorded; a month can only be recorded once.",
  PROTOTYPE_SNAPSHOT_BACKFILL_REFUSED: "Only dates within the last 14 days can be recorded.",
  PROTOTYPE_RECORD_BACKFILL_REFUSED: "Only dates within the last 14 days can be recorded.",
  PROTOTYPE_RECORD_NO_ASSESSED_COMPANIES: "No company could be assessed for that date, so it could never be scored. Refresh prices or pick a later date.",
  PROTOTYPE_SELL_EXCEEDS_HOLDING: "That sale is more shares than you held on that date.",
  PROTOTYPE_INVALID_SYMBOL: "That ticker doesn't look right. Use letters like AAPL (US) or VOD.LSE.",
  PROTOTYPE_INVALID_TRADE_DATE: "The trade date can't be in the future.",
};

/** GET JSON; rejects with the server's error code (or a generic one). */
export function getJson<T>(url: string): Promise<T> {
  return fetch(url, {cache: "no-store"}).then(async response => {
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
    return value as T;
  });
}

/** Plain-English text for a server error code; unknown codes are shown as they are. */
export function friendlyError(code: string): string {
  return MESSAGES[code] ?? code;
}
