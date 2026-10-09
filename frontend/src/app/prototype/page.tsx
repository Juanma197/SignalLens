"use client";
import {FormEvent, Suspense, useRef, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice, Report, RosterView} from "./view";
import {ValueRankingView} from "./value-view";

function PrototypePage() {
  const query=useSearchParams();
  const [cutoff,setCutoff]=useState(query.get("decision_at")??"");
  const [report,setReport]=useState<Report|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(false);
  const requestNumber=useRef(0);
  async function load(event: FormEvent) {
    event.preventDefault(); const number=++requestNumber.current;
    setLoading(true);setError("");setReport(null);
    try {
      const parsed = new Date(cutoff);
      if(!cutoff || !Number.isFinite(parsed.getTime()) || !/(Z|[+-]\d\d:\d\d)$/.test(cutoff)) throw new Error("Use an ISO timestamp with Z or an explicit offset.");
      const response=await fetch(`/api/research/prototype/roster?decision_at=${encodeURIComponent(parsed.toISOString())}`,{cache:"no-store"});
      const value=await response.json();
      if(!response.ok) throw new Error(value.detail?.code??"PROTOTYPE_SERVICE_UNAVAILABLE");
      if(number===requestNumber.current)setReport(value);
    } catch (e) {if(number===requestNumber.current)setError(e instanceof Error?e.message:"PROTOTYPE_SERVICE_UNAVAILABLE");}
    finally {if(number===requestNumber.current)setLoading(false);}
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Research prototype</strong><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link><Link href="/research">Company research</Link></nav><section className="hero compact"><p className="eyebrow">MONTHLY REVIEW · STORED EVIDENCE · READ ONLY</p><h1>Find what is undervalued.<br/><span>See the evidence.</span></h1><p className="lede">Up to three undervalued candidates, with value traps excluded and every reason shown. Below them: the 126-session momentum baseline and the roster review.</p></section><PrototypeNotice/>
    <form onSubmit={load} className="panel research-form"><label>Evidence cutoff (ISO timestamp with timezone)<input type="text" required placeholder="2026-10-01T00:00:00Z" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label><button disabled={loading}>{loading?"Reading stored evidence…":"Review roster and shortlist"}</button></form>
    {error&&<p role="alert" className="notice warning">{error}. No evidence or result has been substituted.</p>}{report&&<>{report.value_ranking&&<ValueRankingView ranking={report.value_ranking} decision={report.decision_at} target={report.target_members}/>}<RosterView report={report}/></>}</main>;
}

export default function Page(){return <Suspense fallback={<p>Loading prototype…</p>}><PrototypePage/></Suspense>;}
