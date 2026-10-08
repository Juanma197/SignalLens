"use client";
import {FormEvent, useCallback, useEffect, useState} from "react";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {call, SnapshotSummary} from "../store-view";

export default function Snapshots() {
  const [items, setItems] = useState<SnapshotSummary[]|null>(null);
  const [cutoff, setCutoff] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => call<{snapshots: SnapshotSummary[]}>("snapshots").then(v => setItems(v.snapshots)).catch(e => setError(e.message)), []);
  useEffect(() => {load();}, [load]);
  async function create(event: FormEvent) {
    event.preventDefault(); setError("");
    if (!/(Z|[+-]\d\d:\d\d)$/.test(cutoff) || !Number.isFinite(new Date(cutoff).getTime())) {setError("Use an ISO timestamp with Z or an explicit offset."); return;}
    if (!window.confirm(`Freeze the shortlist for ${cutoff.slice(0, 7)}? A snapshot cannot be changed or replaced.`)) return;
    setBusy(true);
    try {await call("snapshots", {decision_at: new Date(cutoff).toISOString()}); await load();}
    catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setBusy(false);}
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Monthly snapshots</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/watchlist">Watchlist</Link></nav><PrototypeNotice/>
    <form onSubmit={create} className="panel research-form"><label>Freeze this month&apos;s shortlist at cutoff (no more than 14 days ago; one per month)<input type="text" required placeholder="2026-10-02T12:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)}/></label><button disabled={busy}>{busy ? "Assessing and recording…" : "Record snapshot"}</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    <section className="panel prototype-panel"><h2>Recorded snapshots</h2>
      {items === null ? <p role="status">Reading the prototype store…</p> : items.length === 0 ? <p>No snapshots yet. Missed months are never backfilled.</p> :
        <ul>{items.map(s => <li key={s.snapshot_id}><Link href={`/prototype/snapshots/${s.snapshot_id}`}>{s.month}</Link> — cutoff {s.decision_at}{s.synthetic_fixture ? " · SYNTHETIC" : ""}</li>)}</ul>}</section></main>;
}
