"use client";
import {Suspense, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {cutoffFor} from "../when";
import {call, Note, WatchItem, WatchlistView} from "../store-view";

function Watchlist() {
  const query = useSearchParams();
  // Company links open on the latest data unless the page was opened with a date.
  const [cutoff] = useState(() => query.get("decision_at") || cutoffFor({mode: "latest"}));
  const [data, setData] = useState<{items: WatchItem[]; notes: Note[]}|null>(null);
  const [error, setError] = useState("");
  useEffect(() => {call<{items: WatchItem[]; notes: Note[]}>("watchlist").then(setData).catch(e => setError(e.message));}, []);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Prototype watchlist</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/monthly">This month</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/snapshots">Snapshots</Link></nav><PrototypeNotice/>
    {error ? <p role="alert" className="notice warning">{error}</p> : data ? <WatchlistView items={data.items} notes={data.notes} cutoff={cutoff}/> : <p role="status">Reading the prototype store…</p>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading watchlist…</p>}><Watchlist/></Suspense>;}
