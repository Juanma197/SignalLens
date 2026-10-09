"use client";
import {FormEvent, Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {ChecksOverview, ChecksReport} from "../checks-view";
import {When, WhenPicker, cutoffFor, friendlyError, getJson, whenFromQuery} from "../when";

function ChecksPage() {
  const query = useSearchParams();
  const [when, setWhen] = useState<When>(whenFromQuery(query.get("decision_at")));
  const target = query.get("target_members") ?? "15";
  const [report, setReport] = useState<ChecksReport|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback((chosen: When) =>
    getJson<ChecksReport>(`/api/research/prototype/thesis-checks?decision_at=${encodeURIComponent(cutoffFor(chosen))}&target_members=${encodeURIComponent(target)}`)
      .then(setReport).catch(e => setError(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE")))
      .finally(() => setLoading(false)), [target]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on open with the initial choice
  useEffect(() => {load(when);}, [load]);
  function submit(event: FormEvent) {event.preventDefault(); setLoading(true); setError(""); setReport(null); load(when);}
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Thesis checks</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/monthly">This month</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">HOLDINGS · WATCHLIST · YOUR CONDITIONS</p><h1>Is the thesis still true?<br/><span>Check before the price tells you.</span></h1>
      <p className="lede">Every holding, watched company and company with conditions, re-checked against the stored evidence. Broken theses come first.</p></section>
    <PrototypeNotice/>
    <form onSubmit={submit} className="panel research-form"><WhenPicker when={when} onChange={setWhen} disabled={loading}/><button disabled={loading}>{loading ? "Checking…" : "Update"}</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {report && <ChecksOverview report={report} target={target}/>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading thesis checks…</p>}><ChecksPage/></Suspense>;}
