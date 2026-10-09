"use client";
import {FormEvent, Suspense, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {Monthly, MonthlyView} from "../monthly-view";

function MonthlyPage() {
  const query = useSearchParams();
  const [cutoff, setCutoff] = useState(query.get("decision_at") ?? "");
  const target = query.get("target_members") ?? "15";
  const [monthly, setMonthly] = useState<Monthly|null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [cash, setCash] = useState(query.get("cash") ?? "0");
  const [reinvest, setReinvest] = useState(true);
  async function load(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(""); setMonthly(null);
    try {
      const parsed = new Date(cutoff);
      if (!cutoff || !Number.isFinite(parsed.getTime()) || !/(Z|[+-]\d\d:\d\d)$/.test(cutoff)) throw new Error("Use an ISO timestamp with Z or an explicit offset.");
      const response = await fetch(`/api/research/prototype/monthly?decision_at=${encodeURIComponent(parsed.toISOString())}&target_members=${encodeURIComponent(target)}&cash=${encodeURIComponent(Number(cash) || 0)}&reinvest=${reinvest}`, {cache: "no-store"});
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
      setMonthly(value);
    } catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setLoading(false);}
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>This month</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">MONTHLY DECISIONS · DECISION SUPPORT ONLY</p><h1>What to buy, keep and sell.<br/><span>And why.</span></h1>
      <p className="lede">New undervalued opportunities and a decision for every holding, from your trades, the valuation ranking and your thesis checks at one cutoff. Nothing is executed.</p></section>
    <PrototypeNotice/>
    <form onSubmit={load} className="panel research-form"><label>Evidence cutoff (ISO timestamp with timezone)<input type="text" required placeholder="2026-10-01T00:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)}/></label><label>New money to invest (USD)<input type="number" min="0" step="any" value={cash} onChange={e => setCash(e.target.value)}/></label><label className="prototype-inline-check"><input type="checkbox" checked={reinvest} onChange={e => setReinvest(e.target.checked)}/> Reinvest sale proceeds</label><button disabled={loading}>{loading ? "Working it out…" : "Show this month"}</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {monthly && <MonthlyView monthly={monthly}/>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading this month…</p>}><MonthlyPage/></Suspense>;}
