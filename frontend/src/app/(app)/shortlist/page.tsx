"use client";
import {FormEvent, Suspense, useCallback, useEffect, useRef, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice, Report, RosterView} from "../view";
import {ValueRankingView} from "../value-view";
import {When, WhenPicker, cutoffFor, describeCutoff, friendlyError, getJson, whenFromQuery} from "../when";

function PrototypePage() {
  const query=useSearchParams();
  const [when,setWhen]=useState<When>(whenFromQuery(query.get("decision_at")));
  const [report,setReport]=useState<Report|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);
  const requestNumber=useRef(0);
  const load=useCallback((chosen: When)=>{
    const number=++requestNumber.current;
    return getJson<Report>(`/api/research/prototype/roster?decision_at=${encodeURIComponent(cutoffFor(chosen))}`)
      .then(value=>{if(number===requestNumber.current)setReport(value);})
      .catch(e=>{if(number===requestNumber.current)setError(friendlyError(e instanceof Error?e.message:"PROTOTYPE_SERVICE_UNAVAILABLE"));})
      .finally(()=>{if(number===requestNumber.current)setLoading(false);});
  },[]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on open with the initial choice
  useEffect(()=>{load(when);},[load]);
  function submit(event: FormEvent){event.preventDefault();setLoading(true);setError("");setReport(null);load(when);}
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Research prototype</strong><Link href="/">This month</Link><Link href="/scorecard">Scorecard</Link><Link href="/portfolio">Portfolio</Link><Link href="/checks">Thesis checks</Link><Link href="/watchlist">Watchlist</Link><Link href="/snapshots">Snapshots</Link><Link href="/research">Company research</Link></nav><section className="hero compact"><p className="eyebrow">MONTHLY REVIEW · STORED EVIDENCE · READ ONLY</p><h1>Find what is undervalued.<br/><span>See the evidence.</span></h1><p className="lede">Up to three undervalued candidates, with value traps excluded and every reason shown. Below them: the 126-session momentum baseline and the roster review.</p></section><PrototypeNotice/>
    <form onSubmit={submit} className="panel research-form"><WhenPicker when={when} onChange={setWhen} disabled={loading}/><button disabled={loading}>{loading?"Reading the data…":"Update"}</button>{loading&&<p className="prototype-as-of">The first look at a date reads every company and can take a minute or two; after that it is quick.</p>}</form>
    {report&&<p className="prototype-as-of">Showing data as of {describeCutoff(report.decision_at)}.</p>}
    {error&&<p role="alert" className="notice warning">{error}</p>}{report&&<>{report.value_ranking&&<ValueRankingView ranking={report.value_ranking} decision={report.decision_at} target={report.target_members}/>}<RosterView report={report}/></>}</main>;
}

export default function Page(){return <Suspense fallback={<p>Loading…</p>}><PrototypePage/></Suspense>;}
