"use client";
import {Suspense, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {call, Note, WatchItem, WatchlistView} from "../store-view";

function Watchlist() {
  const query = useSearchParams();
  const [cutoff, setCutoff] = useState(query.get("decision_at") ?? "");
  const [data, setData] = useState<{items: WatchItem[]; notes: Note[]}|null>(null);
  const [error, setError] = useState("");
  useEffect(() => {call<{items: WatchItem[]; notes: Note[]}>("watchlist").then(setData).catch(e => setError(e.message));}, []);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Prototype watchlist</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/snapshots">Snapshots</Link></nav><PrototypeNotice/>
    <form className="panel research-form" onSubmit={e => e.preventDefault()}><label>Evidence cutoff for company links<input type="text" placeholder="2026-10-02T12:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)}/></label></form>
    {error ? <p role="alert" className="notice warning">{error}</p> : data ? <WatchlistView items={data.items} notes={data.notes} cutoff={cutoff}/> : <p role="status">Reading the prototype store…</p>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading watchlist…</p>}><Watchlist/></Suspense>;}
