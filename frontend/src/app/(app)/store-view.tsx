"use client";
import Link from "next/link";
import {FormEvent, useCallback, useEffect, useState} from "react";
import {detailHref, percent} from "./view";

export type Note = {note_id: string; security_id: string; body: string; recorded_at: string};
export type WatchItem = {security_id: string; qualified_symbol: string|null; company_name: string|null; added_at: string; thesis_status?: string|null; thesis_recorded_at?: string|null};
export type SnapshotSummary = {snapshot_id: string; month: string; decision_at: string; created_at: string; version: string; configuration_hash: string; synthetic_fixture: boolean; report_sha256: string};
export type Member = {security_id: string; qualified_symbol: string|null; company_name: string|null; calculation: {momentum_return: number; end_session: string}|null};
export type Snapshot = SnapshotSummary & {integrity_verified: boolean; notice: string; blockers: string[]; eligible_count: number; proposed_membership: string[]; results: string[]; members: Member[]};
export type Checkpoint = {sessions: number; status: "pending"|"available"|"missing_price"|"missing_base_price"; session?: string; sessions_elapsed?: number; return?: number};
export type Tracking = {as_of: string; latest_stored_session: string|null; checkpoints: number[];
  companies: {security_id: string; qualified_symbol: string; company_name: string|null; result: boolean; decision_session: string; checkpoints: Checkpoint[]}[];
  comparison: {sessions: number; results: Group; all_members: Group}[]};
type Group = {available: number; of: number; mean_return: number|null};

export async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/research/prototype/store/${path}`, body === undefined ? {cache: "no-store"} :
    {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE");
  return value as T;
}

const when = (value: string) => value.replace("T", " ").slice(0, 16) + " UTC";

export function NotesList({notes}: {notes: Note[]}) {
  if (notes.length === 0) return <p>No notes yet.</p>;
  return <ul className="prototype-notes">{notes.map(n => <li key={n.note_id}><small>{when(n.recorded_at)}</small><p>{n.body}</p></li>)}</ul>;
}

/** Watchlist toggle and append-only notes for one durable ID. */
export function WatchAndNotes({securityId, symbol, name}: {securityId: string; symbol: string|null; name: string|null}) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [watched, setWatched] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => call<{notes: Note[]; watched: boolean}>(`notes/${encodeURIComponent(securityId)}`)
    .then(v => {setNotes(v.notes); setWatched(v.watched);}).catch(e => setError(e.message)), [securityId]);
  useEffect(() => {load();}, [load]);
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try {await action(); await load();} catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setBusy(false);}
  }
  function save(event: FormEvent) {
    event.preventDefault();
    run(() => call("notes", {security_id: securityId, body: draft}).then(() => setDraft("")));
  }
  return <section className="panel prototype-panel"><h2>Watchlist and notes</h2>
    <p>Stored in the separate prototype database by durable ID. Notes cannot be edited; add a new note to correct one.</p>
    <button disabled={busy} onClick={() => run(() => call("watchlist", {security_id: securityId, action: watched ? "remove" : "add", qualified_symbol: symbol, company_name: name}))}>
      {watched ? "Remove from watchlist" : "Add to watchlist"}</button>
    <form onSubmit={save} className="prototype-note-form"><label>New note<textarea required maxLength={4000} rows={4} value={draft} onChange={e => setDraft(e.target.value)} placeholder="What would make this thesis wrong?"/></label><button disabled={busy || !draft.trim()}>Save note</button></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    <NotesList notes={notes}/></section>;
}

export function WatchlistView({items, notes, cutoff}: {items: WatchItem[]; notes: Note[]; cutoff: string}) {
  if (items.length === 0) return <p className="panel prototype-panel">The watchlist is empty. Add companies from their detail pages.</p>;
  return <div className="brief-stack">{items.map(i => {
    const own = notes.filter(n => n.security_id === i.security_id);
    return <section className="panel prototype-panel" key={i.security_id}><p className="eyebrow">Added {when(i.added_at)} · {i.thesis_status ? `thesis: ${i.thesis_status} (${when(i.thesis_recorded_at!)})` : "no thesis yet"}</p>
      <h2>{i.qualified_symbol ?? i.security_id} {i.company_name && <span>· {i.company_name}</span>}</h2>
      {cutoff ? <Link href={detailHref(i.security_id, cutoff)}>Open evidence at {cutoff}</Link> : <p>Enter a cutoff above to open its evidence.</p>}
      <NotesList notes={own}/></section>;})}</div>;
}

const status = (p: Checkpoint) => p.status === "available" ? percent(p.return!) :
  p.status === "pending" ? `pending (${p.sessions_elapsed} of ${p.sessions})` : p.status.replaceAll("_", " ");
const mean = (g: Group) => g.mean_return === null ? `none available (0 of ${g.of})` : `${percent(g.mean_return)} (${g.available} of ${g.of})`;

export function SnapshotView({snapshot, tracking}: {snapshot: Snapshot; tracking: Tracking}) {
  return <div className="brief-stack">
    {snapshot.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence.</p>}
    <section className="panel prototype-panel"><p className="eyebrow">Snapshot {snapshot.month} · frozen</p><h2>Shortlist as recorded</h2>
      <p>Cutoff {snapshot.decision_at}; recorded {when(snapshot.created_at)}. {snapshot.eligible_count} eligible, {snapshot.proposed_membership.length} members, {snapshot.results.length} results. Version {snapshot.version}.</p>
      <p>{snapshot.integrity_verified ? "Integrity verified: the stored record matches its SHA-256 hash." : "Integrity not verified."} <code>{snapshot.report_sha256}</code></p>
      {snapshot.blockers.length > 0 && <p className="notice warning">Results were withheld: {snapshot.blockers.join("; ")}</p>}</section>
    <section className="panel prototype-panel"><h2>What happened afterwards</h2>
      <p>Return from the snapshot&apos;s last price session to exactly {tracking.checkpoints.join(" / ")} US trading sessions later, using stored adjusted closes. Latest stored session: {tracking.latest_stored_session ?? "none"}. Missing prices are shown, never filled. Description only — zero validation credit.</p>
      <div className="prototype-table-wrap"><table><thead><tr><th>Company</th>{tracking.checkpoints.map(k => <th key={k}>{k} sessions</th>)}</tr></thead><tbody>
        {tracking.companies.map(c => <tr key={c.security_id}><td>{c.result ? <b>{c.qualified_symbol} · result</b> : c.qualified_symbol}<small>{c.company_name}</small></td>{c.checkpoints.map(p => <td key={p.sessions}>{status(p)}{p.session && <small>{p.session}</small>}</td>)}</tr>)}
        <tr><td><b>Average of results</b></td>{tracking.comparison.map(g => <td key={g.sessions}>{mean(g.results)}</td>)}</tr>
        <tr><td><b>Average of all members</b></td>{tracking.comparison.map(g => <td key={g.sessions}>{mean(g.all_members)}</td>)}</tr>
      </tbody></table></div></section></div>;
}

export const THESIS_PROMPTS: [string, string, string][] = [
  ["business", "Business", "What it sells, to whom, and how it makes money."],
  ["financial_health", "Financial health", "Your reading of the stored facts above: profitability, cash, debt."],
  ["why_cheap", "Why it might be cheap", "What the market may be missing, fearing or ignoring."],
  ["catalysts", "Potential catalysts", "What could change the market's view, and roughly when."],
  ["downside", "Downside case", "What happens if you are wrong, and how bad it could be."],
  ["invalidation", "What would invalidate the thesis", "Specific, checkable evidence that would make you drop it."],
  ["assumptions", "Assumptions", "What you are taking as given without evidence."],
];
export type ThesisStatus = "researching"|"active"|"rejected";
export type Thesis = {thesis_id: string; security_id: string; status: ThesisStatus; recorded_at: string} & Record<string, string|null>;

function ThesisText({thesis}: {thesis: Thesis}) {
  const filled = THESIS_PROMPTS.filter(([key]) => thesis[key]);
  return <dl className="prototype-thesis">{filled.map(([key, label]) => <div key={key}><dt>{label}{key === "assumptions" ? " (assumption)" : " (interpretation)"}</dt><dd>{thesis[key]}</dd></div>)}</dl>;
}

export function ThesisHistory({versions}: {versions: Thesis[]}) {
  if (versions.length === 0) return <p>No thesis recorded yet.</p>;
  const [latest, ...older] = versions;
  return <>
    <p className="eyebrow">Current: {latest.status} · recorded {when(latest.recorded_at)}</p><ThesisText thesis={latest}/>
    {older.length > 0 && <details><summary>{older.length} earlier version{older.length === 1 ? "" : "s"}</summary>
      {older.map(v => <div key={v.thesis_id} className="prototype-thesis-version"><p className="eyebrow">{v.status} · {when(v.recorded_at)}</p><ThesisText thesis={v}/></div>)}</details>}
  </>;
}

/** Structured, versioned thesis. Saving records a new version; nothing is overwritten. */
export function ThesisPanel({securityId}: {securityId: string}) {
  const [versions, setVersions] = useState<Thesis[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<ThesisStatus>("researching");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => call<{versions: Thesis[]}>(`theses/${encodeURIComponent(securityId)}`)
    .then(v => setVersions(v.versions)).catch(e => setError(e.message)), [securityId]);
  useEffect(() => {load();}, [load]);
  function edit() {
    const latest = versions[0];
    setDraft(Object.fromEntries(THESIS_PROMPTS.map(([key]) => [key, latest?.[key] ?? ""])));
    setStatus(latest?.status ?? "researching"); setEditing(true);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await call<{versions: Thesis[]}>("theses", {security_id: securityId, status, sections: draft});
      setVersions(result.versions); setEditing(false);
    } catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setBusy(false);}
  }
  return <section className="panel prototype-panel"><h2>Your research thesis</h2>
    <p>Facts are the stored evidence above. Everything in this section is your interpretation or assumption. It is not generated or checked by SignalLens and does not affect the shortlist. Each save keeps the earlier versions.</p>
    {editing ? <form onSubmit={save} className="prototype-note-form">
      <label>Status<select value={status} onChange={e => setStatus(e.target.value as ThesisStatus)}><option value="researching">Researching</option><option value="active">Active thesis</option><option value="rejected">Rejected</option></select></label>
      {THESIS_PROMPTS.map(([key, label, prompt]) => <label key={key}>{label}<small>{prompt}</small><textarea rows={3} maxLength={4000} value={draft[key] ?? ""} onChange={e => setDraft({...draft, [key]: e.target.value})}/></label>)}
      <div><button disabled={busy || !Object.values(draft).some(v => v.trim())}>{busy ? "Saving…" : "Save new version"}</button> <button type="button" onClick={() => setEditing(false)}>Cancel</button></div>
    </form> : <><ThesisHistory versions={versions}/><button onClick={edit}>{versions.length ? "Revise thesis" : "Start a thesis"}</button></>}
    {error && <p role="alert" className="notice warning">{error}</p>}
  </section>;
}
