"use client";
import {FormEvent, Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {Monthly, MonthlyView} from "../monthly-view";
import {When, WhenPicker, cutoffFor, describeCutoff, friendlyError, getJson, whenFromQuery} from "../when";

function MonthlyPage() {
  const query = useSearchParams();
  const [when, setWhen] = useState<When>(whenFromQuery(query.get("decision_at")));
  const target = query.get("target_members") ?? "15";
  const [monthly, setMonthly] = useState<Monthly|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [cash, setCash] = useState(query.get("cash") ?? "0");
  const [reinvest, setReinvest] = useState(true);
  const [recorded, setRecorded] = useState("");
  const [recording, setRecording] = useState(false);
  async function record() {
    if (!monthly || !window.confirm("Record this month's picks and decisions? A month can be recorded once and never changed.")) return;
    setRecording(true); setRecorded("");
    try {
      const response = await fetch("/api/research/prototype/store/decision-records", {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({decision_at: monthly.decision_at, target_members: Number(target), cash: Number(cash) || 0, reinvest})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
      setRecorded(`Recorded ${value.month}. Follow it on the Scorecard.`);
    } catch (e) {setRecorded(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"));} finally {setRecording(false);}
  }
  const load = useCallback((chosen: When, money: string, reinvesting: boolean) =>
    getJson<Monthly>(`/api/research/prototype/monthly?decision_at=${encodeURIComponent(cutoffFor(chosen))}&target_members=${encodeURIComponent(target)}&cash=${encodeURIComponent(Number(money) || 0)}&reinvest=${reinvesting}`)
      .then(setMonthly).catch(e => setError(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE")))
      .finally(() => setLoading(false)), [target]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on open with the initial choices
  useEffect(() => {load(when, cash, reinvest);}, [load]);
  function submit(event: FormEvent) {event.preventDefault(); setLoading(true); setError(""); setMonthly(null); setRecorded(""); load(when, cash, reinvest);}
  return <main className="research-page"><nav><span className="mark">SL</span><strong>This month</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/scorecard">Scorecard</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">MONTHLY DECISIONS · DECISION SUPPORT ONLY</p><h1>What to buy, keep and sell.<br/><span>And why.</span></h1>
      <p className="lede">New undervalued opportunities and a decision for every holding, from your trades, the valuation ranking and your thesis checks at one cutoff. Nothing is executed.</p></section>
    <PrototypeNotice/>
    <form onSubmit={submit} className="panel research-form"><WhenPicker when={when} onChange={setWhen} disabled={loading}/><label>New money to invest (USD)<input type="number" min="0" step="any" value={cash} onChange={e => setCash(e.target.value)}/></label><label className="prototype-inline-check"><input type="checkbox" checked={reinvest} onChange={e => setReinvest(e.target.checked)}/> Reinvest sale proceeds</label><button disabled={loading}>{loading ? "Working it out…" : "Update"}</button></form>
    {monthly && <p className="prototype-as-of">Showing data as of {describeCutoff(monthly.decision_at)}.</p>}
    {error && <p role="alert" className="notice warning">{error}</p>}
    {monthly && <><MonthlyView monthly={monthly}/>
      <section className="panel prototype-panel"><h2>Keep score</h2><p>Recording freezes this month&apos;s picks and decisions so the <Link href="/prototype/scorecard">Scorecard</Link> can measure them later. One record per month, within 14 days of the cutoff; it can never be changed.</p>
        <button onClick={record} disabled={recording}>{recording ? "Recording…" : "Record this month's decisions"}</button>{recorded && <p role="status">{recorded}</p>}</section></>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading this month…</p>}><MonthlyPage/></Suspense>;}
