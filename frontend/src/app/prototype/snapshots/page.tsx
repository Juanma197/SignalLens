"use client";
import {FormEvent, useCallback, useEffect, useState} from "react";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {call, SnapshotSummary} from "../store-view";
import {cutoffFor, describeCutoff, friendlyError} from "../when";

export default function Snapshots() {
  const [items, setItems] = useState<SnapshotSummary[]|null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => call<{snapshots: SnapshotSummary[]}>("snapshots").then(v => setItems(v.snapshots)).catch(e => setError(e.message)), []);
  useEffect(() => {load();}, [load]);
  async function create(event: FormEvent) {
    event.preventDefault(); setError("");
    const cutoff = cutoffFor({mode: "latest"});
    if (!window.confirm(`Freeze this month's shortlist as of now (${describeCutoff(cutoff)})? A month can be recorded once and never changed.`)) return;
    setBusy(true);
    try {await call("snapshots", {decision_at: cutoff}); await load();}
    catch (e) {setError(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"));} finally {setBusy(false);}
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Monthly snapshots</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/monthly">This month</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/watchlist">Watchlist</Link></nav><PrototypeNotice/>
    <form onSubmit={create} className="panel research-form"><p>Freeze this month&apos;s shortlist as of now. One per month; it can never be changed.</p><button disabled={busy}>{busy ? "Recording…" : "Record this month's snapshot"}</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    <section className="panel prototype-panel"><h2>Recorded snapshots</h2>
      {items === null ? <p role="status">Reading the prototype store…</p> : items.length === 0 ? <p>No snapshots yet. Missed months are never backfilled.</p> :
        <ul>{items.map(s => <li key={s.snapshot_id}><Link href={`/prototype/snapshots/${s.snapshot_id}`}>{s.month}</Link> — as of {describeCutoff(s.decision_at)}{s.synthetic_fixture ? " · SYNTHETIC" : ""}</li>)}</ul>}</section></main>;
}
