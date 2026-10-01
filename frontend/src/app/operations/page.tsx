"use client";

import { useCallback, useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import Link from "next/link";

const API = "/api/operator";
const LABEL = "STAGING / RESEARCH ONLY — NOT INVESTMENT ADVICE";
const REGIONS = ["US", "LSE", "TO", "XETRA", "PA"];
type Result = Record<string, unknown>;
type Section = {state: "idle"|"loading"|"success"|"error"; data?: Result; error?: Result};
const idle: Section = {state: "idle"};

function parseSessions(text: string): Record<string, string> | null {
  const entries = text.split(",").map(value => value.trim());
  if (entries.length !== REGIONS.length) return null;
  const result: Record<string, string> = {};
  for (const entry of entries) {
    const match = /^(US|LSE|TO|XETRA|PA)=(\d{4}-\d{2}-\d{2})$/.exec(entry);
    if (!match || result[match[1]] || Number.isNaN(Date.parse(`${match[2]}T00:00:00Z`))) return null;
    result[match[1]] = match[2];
  }
  return REGIONS.every(region => result[region]) ? result : null;
}

async function api(path: string, options: RequestInit = {}, timeout = 8000): Promise<Result> {
  try {
    const response = await fetch(`${API}${path}`, {...options, cache: "no-store", signal: AbortSignal.timeout(timeout)});
    const value = await response.json();
    if (!response.ok) throw value.detail ?? {code: "request_failed", message: "The request failed safely."};
    return value;
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError")
      throw {code: "request_timeout", message: "The operator request reached its time limit."};
    if (typeof error === "object" && error && "code" in error) throw error;
    throw {code: "service_unavailable", message: "The operator API is unavailable."};
  }
}

function Status({section, retry}: {section: Section; retry: () => void}) {
  return <div className="section-status" data-state={section.state}>
    <strong>{section.state}</strong>
    {section.state === "error" && <><pre>{JSON.stringify(section.error, null, 2)}</pre><button onClick={retry}>Retry</button></>}
    {section.state === "success" && <pre>{JSON.stringify(section.data, null, 2)}</pre>}
  </div>;
}

function Summary({section, retry}: {section: Section; retry: () => void}) {
  if (section.state !== "success") return <Status section={section} retry={retry}/>;
  const data = section.data ?? {};
  const counts = (data.counts ?? {}) as Result;
  const backup = (data.backup ?? {}) as Result;
  const progress = (data.security_progress ?? {}) as Result;
  const plan = (data.month_end_plan ?? {}) as Result;
  const selections = Array.isArray(plan.proposed_selections) ? plan.proposed_selections.slice(0, 3) : [];
  if (data.overall === "not_initialized") return <div className="panel ops-card" data-state="not_initialized">
    <p className="eyebrow">STAGING / RESEARCH ONLY</p><h2>not_initialized</h2>
    <p>API available. The research database has not been bootstrapped; no zero-valued research results are shown.</p>
    <p>Backup: {String(backup.status ?? "missing")} · Scheduler: disabled · Production publication: disabled</p>
  </div>;
  return <>
    <div className="ops-grid summary-grid">
      <div className="panel ops-card"><p className="eyebrow">STAGING / RESEARCH ONLY</p><h2>{String(data.overall)}</h2><p>API: available · Database: initialized</p><p>Latest refresh: {String(data.latest_successful_refresh ?? "not_recorded")}</p><p>Journal: {String(data.journal_evidence ?? "not_recorded")}</p></div>
      <div className="panel ops-card"><h2>Market data</h2><p>Price: {String(data.latest_price_date ?? "not_recorded")}</p><p>FX: {String(data.latest_fx_date ?? "not_recorded")}</p></div>
      <div className="panel ops-card"><h2>Research funnel</h2><p>{String(counts.selected ?? "not_recorded")} selected · {String(counts.model_ready ?? "not_assessed")} model-ready · {String(counts.withheld ?? "not_assessed")} withheld</p><p>{String(progress.completed ?? "not_recorded")} completed · {String(progress.permanently_failed ?? "not_recorded")} permanent failures · {String(progress.pending ?? "not_recorded")} pending</p></div>
      <div className="panel ops-card"><h2>Safety state</h2><p>{String(data.next_scheduled_operation ?? "Scheduler disabled")}</p><p>Production publication: disabled</p></div>
      <div className="panel ops-card"><h2>Backup</h2><p>{String(backup.status ?? "not_configured")}</p><p>{String(backup.latest_at ?? "not_recorded")}</p></div>
      <div className="panel ops-card"><h2>Month-end plan</h2><p>{String(plan.state ?? "Not confirmed")}</p>{plan.state === "confirmed" && <ul>{selections.map((item, i) => <li key={i}>{String(item)}</li>)}</ul>}</div>
    </div>
    <details className="panel result"><summary>Diagnostic details</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>
  </>;
}

export default function OperationsPage() {
  const [summary, setSummary] = useState<Section>(idle);
  const [health, setHealth] = useState<Section>(idle);
  const [coverage, setCoverage] = useState<Section>(idle);
  const [fundamentals, setFundamentals] = useState<Section>(idle);
  const [prospective, setProspective] = useState<Section>(idle);
  const [shadow, setShadow] = useState<Section>(idle);
  const [readiness, setReadiness] = useState<Section>(idle);
  const [scoring, setScoring] = useState<Section>(idle);
  const [writeResult, setWriteResult] = useState<Section>(idle);
  const [decisionAt, setDecisionAt] = useState("");
  const [sessions, setSessions] = useState("");
  const [fxDate, setFxDate] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [approvedPlan, setApprovedPlan] = useState<Result | null>(null);

  const load = useCallback(async (path: string, setter: Dispatch<SetStateAction<Section>>) => {
    setter(previous => ({...previous, state: "loading"}));
    try { setter({state: "success", data: await api(path)}); }
    catch (error) { setter(previous => ({...previous, state: "error", error: error as Result})); }
  }, []);
  useEffect(() => {
    load("/summary", setSummary); load("/health", setHealth); load("/coverage", setCoverage); load("/shadow/status", setShadow); load("/us-fundamentals/evidence", setFundamentals); load("/prospective-us-shadow/status", setProspective);
    return () => { setAuthorization(""); setConfirmation(""); setSessions(""); };
  }, [load]);

  async function assessment(kind: "model-readiness"|"research-scoring", setter: (value: Section) => void) {
    setter({state: "loading"});
    try {
      let job = await api(`/assessments/${kind}?decision_at=${encodeURIComponent(decisionAt)}`, {method: "POST"});
      const deadline = Date.now() + 125000;
      while (["queued", "running"].includes(String(job.state)) && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 750));
        job = await api(`/assessments/jobs/${job.job_id}`, {}, 5000);
      }
      if (job.state !== "success") throw (job.error ?? {code: "assessment_timeout", message: "Assessment did not complete."});
      setter({state: "success", data: job.result as Result});
    } catch (error) { setter({state: "error", error: error as Result}); }
  }

  function clearSensitive() { setAuthorization(""); setConfirmation(""); setSessions(""); }
  async function plan() {
    const parsed = parseSessions(sessions);
    if (!parsed) { setWriteResult({state: "error", error: {code: "invalid_explicit_sessions", message: "Provide exactly US, LSE, TO, XETRA and PA dates."}}); clearSensitive(); return; }
    const payload = {decision_at: decisionAt, confirmation: "", expected_session_dates: parsed, latest_required_fx_date: fxDate || null};
    setWriteResult({state: "loading"});
    try { const result = await api("/shadow/plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}, 30000);
      setApprovedPlan({...payload, plan_id: result.plan_id}); setWriteResult({state: "success", data: result});
    } catch (error) { setApprovedPlan(null); setWriteResult({state: "error", error: error as Result}); }
    finally { clearSensitive(); }
  }
  async function createShadow() {
    if (!approvedPlan || confirmation !== "CREATE RESEARCH SHADOW" || !authorization) return;
    setWriteResult({state: "loading"});
    try { setWriteResult({state: "success", data: await api("/shadow/create", {method: "POST", headers: {"Content-Type": "application/json", "X-SignalLens-Operation-Authorization": authorization}, body: JSON.stringify({...approvedPlan, confirmation})}, 30000)}); setApprovedPlan(null); }
    catch (error) { setWriteResult({state: "error", error: error as Result}); }
    finally { clearSensitive(); }
  }
  const isolated = health.data?.database_isolation_confirmed === true;

  return <main className="operations">
    <nav><span className="mark">SL</span><strong>Research operations</strong><Link href="/">Research view</Link></nav>
    <section className="hero compact"><p className="eyebrow">{LABEL}</p><h1>Operate safely.<br/><span>Fail closed.</span></h1></section>
    <Summary section={summary} retry={() => load("/summary", setSummary)}/>
    <section className="panel result"><p className="eyebrow">EXPLORATORY DIAGNOSTICS — NOT A NEW MODEL</p><h2>US fundamentals failure diagnosis</h2>
      <p><strong>Frozen model failed.</strong> Aggregate coverage, family attribution, concentration and defect status only. Raw filings are not displayed.</p>
      <Status section={fundamentals} retry={() => load("/us-fundamentals/evidence", setFundamentals)}/>
      <strong>NO CANDIDATES GENERATED.</strong>
    </section>
    <section className="panel result prospective-research"><p className="eyebrow">PROSPECTIVE PAPER RESEARCH ONLY</p><h2>US price + dilution shadow</h2>
      <p><strong>NOT VALIDATED — NOT INVESTMENT ADVICE.</strong> This evidence series is separate from production predictions and recommendations.</p>
      <Status section={prospective} retry={() => load("/prospective-us-shadow/status", setProspective)}/>
    </section>
    <details><summary>Advanced research operations — read-only assessments permitted in staging</summary>
    <section className="ops-grid">
      <div className="panel ops-card"><h2>Process health</h2><Status section={health} retry={() => load("/health", setHealth)}/></div>
      <div className="panel ops-card"><h2>Coverage</h2><Status section={coverage} retry={() => load("/coverage", setCoverage)}/></div>
      <div className="panel ops-card"><h2>Shadow status</h2><Status section={shadow} retry={() => load("/shadow/status", setShadow)}/></div>
      <div className="panel ops-card"><h2>Long-running assessments</h2>
        <p><strong>Read-only in staging.</strong> These assessments may run; they do not persist job state or modify research or production data.</p>
        <label>Decision time (UTC)<input name="ops_decision_clock" autoComplete="off" type="datetime-local" value={decisionAt} onChange={e => setDecisionAt(e.target.value ? `${e.target.value}:00Z` : "")}/></label>
        <button disabled={!decisionAt || readiness.state === "loading"} onClick={() => assessment("model-readiness", setReadiness)}>Model readiness</button>
        <Status section={readiness} retry={() => assessment("model-readiness", setReadiness)}/>
        <button disabled={!decisionAt || scoring.state === "loading"} onClick={() => assessment("research-scoring", setScoring)}>Research scoring</button>
        <Status section={scoring} retry={() => assessment("research-scoring", setScoring)}/>
      </div>
      <div className="panel ops-card"><h2>Month-end shadow plan</h2>
        <label>Explicit sessions<input name="ops_region_calendar" autoComplete="off" data-lpignore="true" value={sessions} onChange={e => setSessions(e.target.value)} placeholder="US=2026-09-30,LSE=2026-09-30,TO=2026-09-30,XETRA=2026-09-30,PA=2026-09-30"/></label>
        <label>Required FX date<input name="ops_fx_calendar" autoComplete="off" type="date" value={fxDate} onChange={e => setFxDate(e.target.value)}/></label>
        <button disabled={!decisionAt || !isolated || !parseSessions(sessions)} onClick={plan}>Plan (read only)</button>
      </div>
      <div className="panel ops-card danger"><h2>Create research shadow</h2>
        <label>Separate authorization<input name="ops_deliberate_key" type="password" autoComplete="new-password" data-lpignore="true" value={authorization} onChange={e => setAuthorization(e.target.value)}/></label>
        <label>Type CREATE RESEARCH SHADOW<input name="ops_deliberate_phrase" autoComplete="off" data-lpignore="true" value={confirmation} onChange={e => setConfirmation(e.target.value)}/></label>
        <button disabled={!isolated || !approvedPlan || confirmation !== "CREATE RESEARCH SHADOW" || !authorization} onClick={createShadow}>Create shadow vintage</button>
        {!approvedPlan && <small>Disabled until an immediately preceding plan succeeds.</small>}
      </div>
    </section>
    <section className="panel result"><h2>Latest write operation</h2><Status section={writeResult} retry={plan}/></section>
    </details>
    <footer>{LABEL}. Production publishing remains unavailable.</footer>
  </main>;
}
