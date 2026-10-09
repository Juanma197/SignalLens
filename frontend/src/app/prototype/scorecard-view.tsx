import {percent} from "./view";

type Point = {sessions: number; status: "available"|"pending"|"missing_price"|"no_base_session"; session?: string; sessions_elapsed?: number; return?: number; excess?: number; right?: boolean};
type Item = {kind: "pick"|"holding"; qualified_symbol: string; company_name?: string|null; decision: string|null; recommendation?: string|null; rank?: number|null; group: string|null; checkpoints: Point[]};
type Month = {record_id: string; month: string; decision_at: string; base_session: string|null; benchmark: {sessions: number; return: number|null; available: number; of: number}[]; items: Item[]};
type Summary = {group: string; sessions: number; scored: number; right: number; hit_rate: number|null; mean_excess: number|null};
export type Scorecard = {checkpoints: number[]; groups: Record<string, string>; benchmark: string; rule: string; label: string; months: Month[]; summary: Summary[]; latest_stored_session?: string|null};

const signed = (v: number|null|undefined) => v == null ? "—" : `${v > 0 ? "+" : ""}${percent(v)}`;
const SESSIONS_LABEL: Record<number, string> = {21: "~1 month", 63: "~3 months", 126: "~6 months", 252: "~12 months"};

function cell(p: Point) {
  if (p.status === "pending") return <span className="prototype-muted">pending ({p.sessions_elapsed}/{p.sessions})</span>;
  if (p.status !== "available") return <span className="prototype-muted">{p.status.replaceAll("_", " ")}</span>;
  return <>{signed(p.return)}{p.excess !== undefined && <small className={p.right ? "prototype-check-holds" : "prototype-check-broken"}>{p.right ? "✓" : "✗"} {signed(p.excess)} vs benchmark</small>}</>;
}

export function ScorecardView({card}: {card: Scorecard}) {
  if (card.months.length === 0) return <section className="panel prototype-panel"><h2>No recorded months yet</h2>
    <p>On <b>This month</b>, press <b>Record this month&apos;s decisions</b> after reviewing them. Each month can be recorded once, within 14 days of its cutoff, and can never be changed. Results appear here as sessions pass.</p></section>;
  return <div className="brief-stack">
    <section className="panel prototype-panel"><h2>Was SignalLens right?</h2>
      <p>{card.rule} {card.benchmark} Latest stored session: {card.latest_stored_session ?? "none"}.</p>
      <div className="prototype-table-wrap"><table className="prototype-value-table"><thead><tr><th>Calls</th>{card.checkpoints.map(k => <th key={k}>{SESSIONS_LABEL[k] ?? `${k} sessions`}</th>)}</tr></thead><tbody>
        {Object.entries(card.groups).map(([group, label]) => <tr key={group}><td>{label}</td>{card.checkpoints.map(k => {
          const s = card.summary.find(x => x.group === group && x.sessions === k)!;
          return <td key={k}>{s.scored === 0 ? <span className="prototype-muted">none scored yet</span> : <>{s.right} of {s.scored} right ({percent(s.hit_rate!)})<small>average {signed(s.mean_excess)} vs benchmark</small></>}</td>;})}</tr>)}
      </tbody></table></div>
      <p><small>{card.label}</small></p></section>
    {[...card.months].reverse().map(m => <section key={m.record_id} className="panel prototype-panel"><p className="eyebrow">Recorded month {m.month} · base session {m.base_session ?? "none"}</p><h2>{m.month}</h2>
      <div className="prototype-table-wrap"><table><thead><tr><th>Call</th>{card.checkpoints.map(k => <th key={k}>{SESSIONS_LABEL[k] ?? `${k} sessions`}</th>)}</tr></thead><tbody>
        {m.items.map(i => <tr key={i.kind + i.qualified_symbol}><td><b>{i.qualified_symbol}</b><small>{i.kind === "pick" ? `Pick #${i.rank} · ${i.recommendation === "strong_buy" ? "Strong Buy" : "Buy"}` : i.decision}{i.group === null ? " · not scored" : ""}</small></td>{i.checkpoints.map(p => <td key={p.sessions}>{cell(p)}</td>)}</tr>)}
        <tr><td><b>Benchmark</b><small>equal-weight assessed companies</small></td>{m.benchmark.map(b => <td key={b.sessions}>{b.return == null ? <span className="prototype-muted">—</span> : <>{signed(b.return)}<small>{b.available} of {b.of} priced</small></>}</td>)}</tr>
      </tbody></table></div></section>)}
  </div>;
}
