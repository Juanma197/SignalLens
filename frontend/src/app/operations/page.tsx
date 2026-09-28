"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const API = "/api/operator";
const LABEL = "RESEARCH ONLY — NOT INVESTMENT ADVICE";
type Result = Record<string, unknown>;

export default function OperationsPage() {
  const [status, setStatus] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [decisionAt, setDecisionAt] = useState("");
  const [sessions, setSessions] = useState("");
  const [fxDate, setFxDate] = useState("");

  async function request(path: string, method = "GET", body?: Result) {
    setBusy(true); setResult(null);
    try {
      const response = await fetch(`${API}${path.replace("/api/v1/operations", "")}`, {
        method, headers: {"Content-Type": "application/json",
          ...(authorization ? {"X-SignalLens-Operation-Authorization": authorization} : {})},
        body: body ? JSON.stringify(body) : undefined,
      });
      const value = await response.json();
      setResult(response.ok ? value : {status: "failed", error: value.detail ?? {code: "request_failed"}});
    } catch { setResult({status: "failed", error: {code: "service_unavailable", message: "The operator API is unavailable."}}); }
    finally { setBusy(false); }
  }

  useEffect(() => { fetch(`${API}/health`).then(r => r.json()).then(setStatus)
    .catch(() => setStatus({status: "unavailable"})); }, []);
  const explicitSessions = Object.fromEntries(sessions.split(",").map(x => x.trim()).filter(Boolean)
    .map(x => x.split("=").map(y => y.trim())).filter(x => x.length === 2));
  const payload = {decision_at: decisionAt, confirmation,
    expected_session_dates: explicitSessions, latest_required_fx_date: fxDate || null};
  const isolated = status?.database_isolation_confirmed === true;
  const monthReady = (result?.month_end_readiness as {confirmed?: boolean} | undefined)?.confirmed === true;
  const summary = (status?.summary ?? {}) as Record<string, unknown>;

  return <main className="operations">
    <nav><span className="mark">SL</span><strong>Research operations</strong><Link href="/">Research view</Link></nav>
    <section className="hero compact"><p className="eyebrow">{LABEL}</p><h1>Operate safely.<br/><span>Fail closed.</span></h1>
      <p className="lede">Read-only diagnostics, bounded planning, and deliberately authorized research writes. Proposed and shadow securities are never recommendations.</p></section>
    <section className="ops-grid">
      <div className="panel ops-card"><h2>System status</h2>
        <dl><dt>Research database</dt><dd>{status?.research_database_available ? "Available" : "Unavailable"}</dd>
          <dt>Database isolation</dt><dd>{isolated ? "Confirmed" : "Not confirmed"}</dd>
          <dt>Production publishing</dt><dd>Unavailable</dd>
          <dt>Latest catalogue retrieval</dt><dd>{String(summary.latest_catalogue_retrieval ?? "Unavailable")}</dd>
          <dt>Price / FX freshness</dt><dd>{summary.price_freshness ? "Reported below" : "Unavailable"} / {summary.fx_freshness ? "Reported below" : "Unavailable"}</dd>
          <dt>Selected / ready / withheld / failed</dt><dd>{[summary.selected_count, summary.model_ready_count, summary.withheld_count, summary.permanently_failed_count].map(x => String(x ?? "—")).join(" / ")}</dd>
          <dt>Latest ingestion / refresh</dt><dd>{String(summary.latest_ingestion_result ?? "Unavailable")}</dd>
          <dt>Shadow vintage status</dt><dd>{String(summary.shadow_vintage_count ?? 0)} vintages</dd>
          <dt>Strategy version</dt><dd>{String(summary.strategy_version ?? "No vintage")}</dd>
          <dt>Configuration hash</dt><dd>{String(summary.configuration_hash ?? "No vintage")}</dd>
          <dt>Evidence gate state</dt><dd>{summary.evidence_gates ? "Reported below" : "Unavailable"}</dd>
          <dt>Upcoming maturity</dt><dd>126 / 252 explicit sessions</dd></dl>
        <button disabled={busy} onClick={() => request("/api/v1/operations/health")}>Refresh status</button>
      </div>
      <div className="panel ops-card"><h2>Read-only checks</h2><p>Fingerprint-protected; writes neither database.</p>
        <label>Decision time (UTC)<input type="datetime-local" value={decisionAt} onChange={e => setDecisionAt(e.target.value ? `${e.target.value}:00Z` : "")}/></label>
        <div className="button-row"><button disabled={busy || !decisionAt} onClick={() => request(`/api/v1/operations/model-readiness?decision_at=${encodeURIComponent(decisionAt)}`)}>Model readiness</button>
          <button disabled={busy || !decisionAt} onClick={() => request(`/api/v1/operations/research-scoring?decision_at=${encodeURIComponent(decisionAt)}`)}>Evidence gates</button></div>
      </div>
      <div className="panel ops-card"><h2>Month-end shadow plan</h2><p>Expected requests: 0. Target: research database. Write: no.</p>
        <label>Explicit sessions (REGION=YYYY-MM-DD, …)<input value={sessions} onChange={e => setSessions(e.target.value)} placeholder="US=2026-10-30,LSE=2026-10-30"/></label>
        <label>Required FX date<input type="date" value={fxDate} onChange={e => setFxDate(e.target.value)}/></label>
        <button disabled={busy || !decisionAt || !isolated} onClick={() => request("/api/v1/operations/shadow/plan", "POST", payload)}>Plan (read only)</button>
      </div>
      <div className="panel ops-card danger"><h2>Create research shadow</h2>
        <p>Operation: persist full internal scores transactionally. Target: research database only. Production publishing: unavailable. Strategy version is fixed by the plan.</p>
        <label>Separate authorization<input type="password" autoComplete="off" value={authorization} onChange={e => setAuthorization(e.target.value)}/></label>
        <label>Type CREATE RESEARCH SHADOW<input value={confirmation} onChange={e => setConfirmation(e.target.value)}/></label>
        <button disabled={busy || !isolated || !monthReady || confirmation !== "CREATE RESEARCH SHADOW" || !authorization}
          onClick={() => request("/api/v1/operations/shadow/create", "POST", payload)}>Create shadow vintage</button>
        {!monthReady && <small>Disabled until an immediately preceding plan confirms explicit month-end price and FX readiness.</small>}
      </div>
    </section>
    <section className="panel result"><header><div><p className="eyebrow">STRUCTURED RESULT</p><h2>{busy ? "Operation in progress…" : "Latest operation"}</h2></div></header>
      <pre aria-live="polite">{JSON.stringify(result ?? status ?? {status: "loading"}, null, 2)}</pre></section>
    <footer>{LABEL}. Current catalogue membership is not survivorship-free. Fundamentals are unavailable under the current EODHD entitlement.</footer>
  </main>;
}
