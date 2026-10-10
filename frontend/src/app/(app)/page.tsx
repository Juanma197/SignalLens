"use client";
import {FormEvent, Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {type Activity, DashboardView, greeting} from "./dashboard-view";
import {Monthly} from "./monthly-view";
import {money} from "./portfolio-view";
import {When, WhenPicker, cutoffFor, describeCutoff, friendlyError, getJson, whenFromQuery} from "./when";

const OWNER = "Juan";

function MonthlyPage() {
  const query = useSearchParams();
  const [when, setWhen] = useState<When>(whenFromQuery(query.get("decision_at")));
  const target = query.get("target_members") ?? "15";
  const [monthly, setMonthly] = useState<Monthly|null>(null);
  const [activity, setActivity] = useState<Activity|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [contribution, setContribution] = useState(query.get("include_contribution") === "true");
  // Buys use confirmed cash only: sale proceeds count once the sale is recorded.
  const reinvest = false;
  const [recorded, setRecorded] = useState("");
  const [recording, setRecording] = useState(false);
  const [hello, setHello] = useState("Hello");
  async function record() {
    if (!monthly || !window.confirm("Record this month's picks and decisions? A month can be recorded once and never changed.")) return;
    setRecording(true); setRecorded("");
    try {
      const response = await fetch("/api/research/prototype/store/decision-records", {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({decision_at: monthly.decision_at, target_members: Number(target), include_contribution: contribution, reinvest})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
      setRecorded(`Recorded ${value.month}. Follow it on the History page.`);
    } catch (e) {setRecorded(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"));} finally {setRecording(false);}
  }
  const load = useCallback((chosen: When, including: boolean, reinvesting: boolean) =>
    getJson<Monthly>(`/api/research/prototype/monthly?decision_at=${encodeURIComponent(cutoffFor(chosen))}&target_members=${encodeURIComponent(target)}&include_contribution=${including}&reinvest=${reinvesting}`)
      .then(setMonthly).catch(e => setError(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE")))
      .finally(() => setLoading(false)), [target]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the greeting depends on the viewer's clock, unknown at render on the server
    setHello(greeting(new Date().getHours()));
    load(when, contribution, reinvest);
    // Activity is a nice-to-have: a failure leaves the panel with the analysis entries only.
    getJson<Activity>("/api/research/prototype/store/portfolio").then(setActivity).catch(() => setActivity(null));
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on open with the initial choices
  }, [load]);
  function refresh(event?: FormEvent) {event?.preventDefault(); setLoading(true); setError(""); setMonthly(null); setRecorded(""); load(when, contribution, reinvest);}
  const toolbar = <details className="dash-options"><summary>Options</summary>
    <form onSubmit={refresh} className="dash-options-form"><WhenPicker when={when} onChange={setWhen} disabled={loading}/>
      <label className="prototype-inline-check"><input type="checkbox" checked={contribution} onChange={e => setContribution(e.target.checked)}/> Include this month&apos;s planned contribution{monthly?.allocation?.account ? ` (${money(monthly.allocation.account.monthly_contribution, monthly.allocation.account.currency)})` : ""}, not yet deposited</label>
      <button disabled={loading}>Apply</button></form></details>;
  const keepScore = <section className="card"><header><h2>Keep score</h2></header>
    <p className="muted">Recording freezes this month&apos;s picks and decisions so the <Link href="/scorecard">History</Link> page can measure them later, and next month can show what changed. One record per month, within 14 days of the cutoff; it can never be changed.</p>
    <button onClick={record} disabled={recording}>{recording ? "Recording…" : "Record this month's decisions"}</button>{recorded && <p role="status">{recorded}</p>}</section>;
  return <main className="dash-page">
    <header className="dash-head">
      <div><h1>{hello}, {OWNER}</h1><p>Here&apos;s your investment overview and this month&apos;s recommendations. Decision support only: nothing is executed.</p></div>
      <div className="dash-head-actions">
        {monthly && <p className="updated">Data as of<br/><b>{describeCutoff(monthly.decision_at)}</b></p>}
        <button type="button" className="button-outline" onClick={() => refresh()} disabled={loading}>↻ {loading ? "Working it out…" : "Refresh analysis"}</button>
      </div>
    </header>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {!monthly && !error && <p role="status" className="muted">Working out this month&apos;s picks and decisions…</p>}
    {monthly && <DashboardView m={monthly} activity={activity} toolbar={toolbar} keepScore={keepScore}/>}
  </main>;
}
export default function Page() {return <Suspense fallback={<p>Loading this month…</p>}><MonthlyPage/></Suspense>;}
