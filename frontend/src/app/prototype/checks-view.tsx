"use client";
import Link from "next/link";
import {FormEvent, useCallback, useEffect, useState} from "react";
import {call} from "./store-view";
import {detailHref} from "./view";

export type Metric = {metric: string; label: string; unit: "fraction"|"ratio"|"usd"|"usd_per_share"; source: string};
export type Check = {metric: string; comparator: "at_least"|"at_most"; threshold: number; note: string|null};
export type CheckResult = Check & {label: string; unit: Metric["unit"]; value: number|null; status: "holds"|"broken"|"unknown"; reason: string|null; fiscal_year_end: string|null};
export type Overall = "broken"|"warning"|"unknown"|"intact"|"not_covered";
export type CompanyChecks = {security_id: string; qualified_symbol?: string|null; company_name?: string|null; overall: Overall; interest: string[];
  fiscal_year_end?: string|null; checks: CheckResult[]; automatic: {kind: string; severity: "broken"|"warning"|"unknown"; text: string}[]; has_own_checks: boolean};
export type ChecksReport = {decision_at: string; method: string; synthetic_fixture: boolean; metrics: Metric[]; companies: CompanyChecks[]; uncovered_holdings: string[]};
type CheckSet = {set_id: string; recorded_at: string; checks: Check[]};

// Thresholds are typed in display units: percentages, millions of dollars, or plain numbers.
const SCALE = {fraction: 100, ratio: 1, usd: 1e-6, usd_per_share: 1};
const SUFFIX = {fraction: "%", ratio: "×", usd: "M USD", usd_per_share: "USD"};
export const display = (value: number, unit: Metric["unit"]) => `${Number((value * SCALE[unit]).toFixed(2)).toLocaleString("en-US")}${unit === "fraction" ? "%" : " " + SUFFIX[unit]}`;
export const OVERALL: Record<Overall, string> = {broken: "Thesis broken", warning: "Warning", unknown: "Not fully checkable", intact: "Intact", not_covered: "Not covered by SignalLens data"};
const ICON = {holds: "✓", broken: "✗", unknown: "?", warning: "⚠"};

export function CheckResults({result}: {result: CompanyChecks}) {
  return <>
    <p className={`prototype-overall prototype-overall-${result.overall}`}><b>{OVERALL[result.overall]}</b>{result.fiscal_year_end && <> · figures for fiscal year ending {result.fiscal_year_end}</>}</p>
    {result.checks.length > 0 && <ul className="prototype-checks">{result.checks.map((c, i) => <li key={i} className={`prototype-check-${c.status}`}>
      {ICON[c.status]} {c.label} {c.comparator === "at_least" ? "at least" : "at most"} {display(c.threshold, c.unit)}: {c.value === null ? c.reason : <>now <b>{display(c.value, c.unit)}</b></>}
      {c.note && <small>{c.note}</small>}</li>)}</ul>}
    {result.automatic.length > 0 && <ul className="prototype-checks">{result.automatic.map((a, i) => <li key={i} className={`prototype-check-${a.severity}`}>{a.severity === "broken" ? ICON.broken : a.severity === "warning" ? ICON.warning : ICON.unknown} {a.text} <small>Automatic check</small></li>)}</ul>}
    {result.checks.length === 0 && result.automatic.length === 0 && result.overall !== "not_covered" && <p>No warning signs and no conditions of your own yet.</p>}
  </>;
}

type Row = {metric: string; comparator: Check["comparator"]; threshold: string; note: string};

/** Company page: the operator's conditions (versioned) and their status at this cutoff. */
export function ThesisChecksPanel({securityId, cutoff, target}: {securityId: string; cutoff: string; target: string}) {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [versions, setVersions] = useState<CheckSet[]>([]);
  const [result, setResult] = useState<CompanyChecks|null>(null);
  const [rows, setRows] = useState<Row[]|null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const evaluateNow = useCallback(() => fetch(`/api/research/prototype/thesis-checks?decision_at=${encodeURIComponent(cutoff)}&target_members=${encodeURIComponent(target)}&security_id=${encodeURIComponent(securityId)}`, {cache: "no-store"})
    .then(async r => {const v = await r.json(); if (!r.ok) throw new Error(v.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE"); setResult(v.companies[0]);}), [securityId, cutoff, target]);
  const load = useCallback(() => Promise.all([
    call<{metrics: Metric[]; versions: CheckSet[]}>(`checks/${encodeURIComponent(securityId)}`).then(v => {setMetrics(v.metrics); setVersions(v.versions);}),
    evaluateNow()]).catch(e => setError(e.message)), [securityId, evaluateNow]);
  useEffect(() => {load();}, [load]);
  const unit = (metric: string) => metrics.find(m => m.metric === metric)?.unit ?? "ratio";
  function edit() {
    setRows((versions[0]?.checks ?? []).map(c => ({metric: c.metric, comparator: c.comparator, threshold: String(Number((c.threshold * SCALE[unit(c.metric)]).toFixed(6))), note: c.note ?? ""})));
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const checks = rows!.map(r => ({metric: r.metric, comparator: r.comparator, threshold: Number(r.threshold) / SCALE[unit(r.metric)], note: r.note || null}));
      const saved = await call<{versions: CheckSet[]}>("checks", {security_id: securityId, checks});
      setVersions(saved.versions); setRows(null); await evaluateNow();
    } catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE");} finally {setBusy(false);}
  }
  const update = (i: number, patch: Partial<Row>) => setRows(rs => rs!.map((r, j) => j === i ? {...r, ...patch} : r));
  return <section className="panel prototype-panel"><h2>Thesis checks</h2>
    <p>Turn your thesis into conditions SignalLens can test, for example &ldquo;operating margin at least 10%&rdquo;. They are re-checked against the stored evidence at every cutoff, together with automatic warning signs. A thesis can break while the price holds up; the price you paid is never used.</p>
    {result && <CheckResults result={result}/>}
    {rows ? <form onSubmit={save} className="prototype-check-form">
      {rows.map((r, i) => <div key={i} className="prototype-check-row">
        <select aria-label="Metric" value={r.metric} onChange={e => update(i, {metric: e.target.value})}>{metrics.map(m => <option key={m.metric} value={m.metric}>{m.label}</option>)}</select>
        <select aria-label="Comparison" value={r.comparator} onChange={e => update(i, {comparator: e.target.value as Check["comparator"]})}><option value="at_least">at least</option><option value="at_most">at most</option></select>
        <label className="prototype-threshold"><input aria-label="Threshold" required type="number" step="any" value={r.threshold} onChange={e => update(i, {threshold: e.target.value})}/><span>{SUFFIX[unit(r.metric)]}</span></label>
        <input aria-label="Why this matters" maxLength={500} placeholder="Why this matters (optional)" value={r.note} onChange={e => update(i, {note: e.target.value})}/>
        <button type="button" onClick={() => setRows(rs => rs!.filter((_, j) => j !== i))}>Remove</button></div>)}
      <div><button type="button" disabled={rows.length >= 20} onClick={() => setRows(rs => [...rs!, {metric: metrics[0]?.metric ?? "operating_margin", comparator: "at_least", threshold: "", note: ""}])}>Add condition</button> <button disabled={busy}>{busy ? "Saving…" : "Save conditions"}</button> <button type="button" onClick={() => setRows(null)}>Cancel</button></div>
    </form> : <button onClick={edit} disabled={metrics.length === 0}>{versions[0]?.checks.length ? "Edit conditions" : "Add conditions"}</button>}
    {versions.length > 1 && <p><small>{versions.length} saved versions; each save keeps the earlier ones.</small></p>}
    {error && <p role="alert" className="notice warning">{error}</p>}
  </section>;
}

export function ChecksOverview({report, target}: {report: ChecksReport; target: string}) {
  const counts = (o: Overall) => report.companies.filter(c => c.overall === o).length;
  return <div className="brief-stack">
    {report.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence.</p>}
    <section className="panel prototype-panel"><h2>Thesis health at {report.decision_at}</h2>
      <p>{counts("broken")} broken · {counts("warning")} warning · {counts("unknown")} not fully checkable · {counts("intact")} intact. {report.method}</p>
      {report.uncovered_holdings.length > 0 && <p className="notice warning">Held but outside SignalLens data, so not checked: {report.uncovered_holdings.join(", ")}.</p>}
      {report.companies.length === 0 && <p>Nothing to check yet. Holdings, watchlist companies and companies with conditions appear here.</p>}</section>
    {report.companies.map(c => <section key={c.security_id} className="panel prototype-panel">
      <p className="eyebrow">{c.interest.length ? c.interest.join(" · ") : "conditions set"}{!c.has_own_checks && c.overall !== "not_covered" ? " · no conditions of your own yet" : ""}</p>
      <h2>{c.overall === "not_covered" ? c.security_id : <Link href={detailHref(c.security_id, report.decision_at, Number(target))}>{c.qualified_symbol}</Link>} {c.company_name && <span>· {c.company_name}</span>}</h2>
      <CheckResults result={c}/></section>)}
  </div>;
}
