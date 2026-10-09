"use client";
import {FormEvent, Suspense, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {ChecksOverview, ChecksReport} from "../checks-view";

function ChecksPage() {
  const query = useSearchParams();
  const [cutoff, setCutoff] = useState(query.get("decision_at") ?? "");
  const target = query.get("target_members") ?? "15";
  const [report, setReport] = useState<ChecksReport|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(""); setReport(null);
    try {
      const parsed = new Date(cutoff);
      if (!cutoff || !Number.isFinite(parsed.getTime()) || !/(Z|[+-]\d\d:\d\d)$/.test(cutoff)) throw new Error("Use an ISO timestamp with Z or an explicit offset.");
      const response = await fetch(`/api/research/prototype/thesis-checks?decision_at=${encodeURIComponent(parsed.toISOString())}&target_members=${encodeURIComponent(target)}`, {cache: "no-store"});
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
      setReport(value);
    } catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setLoading(false);}
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Thesis checks</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">HOLDINGS · WATCHLIST · YOUR CONDITIONS</p><h1>Is the thesis still true?<br/><span>Check before the price tells you.</span></h1>
      <p className="lede">Every holding, watched company and company with conditions, re-checked against the stored evidence. Broken theses come first.</p></section>
    <PrototypeNotice/>
    <form onSubmit={load} className="panel research-form"><label>Evidence cutoff (ISO timestamp with timezone)<input type="text" required placeholder="2026-10-01T00:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)}/></label><button disabled={loading}>{loading ? "Checking…" : "Check theses"}</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {report && <ChecksOverview report={report} target={target}/>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading thesis checks…</p>}><ChecksPage/></Suspense>;}
