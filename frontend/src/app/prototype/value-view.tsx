import Link from "next/link";
import {useState} from "react";
import {detailHref, percent, useShown} from "./view";
import {describeCutoff} from "./when";

export type ValueStatus = "candidate"|"watch"|"not_undervalued"|"value_trap"|"not_assessable";
export type ValueAssessment = {security_id: string; qualified_symbol: string; company_name: string|null; status: ValueStatus; reasons: string[]; rank?: number|null;
  recommendation?: "strong_buy"|"buy"; upside?: number; cautious_vs_price?: number|null; optimistic_vs_price?: number|null; measure?: string; price?: number;
  price_session?: string; middle_value_per_share?: number|null; conviction?: "high"|"medium"|"low"; conviction_points?: number; conviction_for?: string[];
  conviction_against?: string[]; risk?: "low"|"medium"|"high"; risks?: string[]; score?: number; value_traps?: string[]};
export type ValueRanking = {population: number; picks: string[]; companies: ValueAssessment[]; method: string; label: string;
  rules: {minimum_upside: number; strong_upside: number; maximum_picks: number}};

const RECOMMENDATION = {strong_buy: "Strong Buy", buy: "Buy"};
const STATUS: Record<ValueStatus, string> = {candidate: "Candidate", watch: "Watch — cheap but not convincing", not_undervalued: "Not undervalued",
  value_trap: "Value trap — excluded", not_assessable: "Not assessable"};
const signed = (value: number|null|undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${percent(value)}`;
const cap = (value: string) => value[0].toUpperCase() + value.slice(1);

function PickCard({a, decision, target}: {a: ValueAssessment; decision: string; target: number}) {
  return <article className={a.rank === 1 ? "prototype-pick prototype-pick-first" : "prototype-pick"}>
    <p className="eyebrow">#{a.rank}{a.rank === 1 ? " · top opportunity" : ""}</p>
    <h3><Link href={detailHref(a.security_id, decision, target)}>{a.qualified_symbol}</Link></h3>
    <p>{a.company_name}</p>
    <p className="prototype-pick-line"><b>{RECOMMENDATION[a.recommendation!]}</b> · estimated upside <b>{signed(a.upside)}</b></p>
    <p>Range: cautious {signed(a.cautious_vs_price)} · optimistic {signed(a.optimistic_vs_price)} (from {a.measure}). Price ${a.price?.toFixed(2)} on {a.price_session}.</p>
    <p>Conviction <b>{a.conviction}</b> ({a.conviction_points}/5) · risk <b>{a.risk}</b></p>
    <ul>{a.conviction_for!.map(t => <li key={t}>✓ {t}</li>)}{a.conviction_against!.map(t => <li key={t}>✗ {t}</li>)}{a.risks!.map(t => <li key={t}>⚠ {t}</li>)}</ul>
  </article>;
}

export function ValueRankingView({ranking, decision, target}: {ranking: ValueRanking; decision: string; target: number}) {
  const picks = ranking.companies.filter(a => a.rank != null);
  const [find, setFind] = useState("");
  const needle = find.trim().toLowerCase();
  const [rows, more] = useShown(needle ? ranking.companies.filter(a => `${a.qualified_symbol} ${a.company_name ?? ""}`.toLowerCase().includes(needle)) : ranking.companies);
  return <section className="panel prototype-panel"><p className="eyebrow">THIS MONTH · UNDERVALUATION RANKING</p><h2>Top {ranking.rules.maximum_picks} undervalued candidates</h2>
    <p>Ranked from {ranking.population} eligible companies, as of {describeCutoff(decision)}. {ranking.label}</p>
    {ranking.population < 10 && <p className="notice warning">Only {ranking.population} companies could be assessed. With so few, a pick is the best of a small set, not of the market.</p>}
    {picks.length === 0 ? <p>No company passes the filters this month. Nothing is forced: holding cash or current positions is a valid outcome.</p>
      : <div className="prototype-cards prototype-picks">{picks.map(a => <PickCard key={a.security_id} a={a} decision={decision} target={target}/>)}</div>}
    {picks.length > 0 && picks.length < ranking.rules.maximum_picks && <p>Only {picks.length} {picks.length === 1 ? "company qualifies" : "companies qualify"}; the remaining places are left empty rather than filled with weaker names.</p>}
    <h3>Every eligible company</h3>
    {ranking.companies.length > 25 && <p><label>Find a company <input type="search" value={find} onChange={e => setFind(e.target.value)} placeholder="Symbol or name"/></label></p>}
    <div className="prototype-table-wrap"><table className="prototype-value-table"><thead><tr><th>Rank</th><th>Company</th><th>Outcome</th><th>Upside</th><th>Conviction</th><th>Risk</th><th>Why</th></tr></thead><tbody>
      {rows.map(a => <tr key={a.security_id}><td>{a.rank ?? "—"}</td>
        <td><Link href={detailHref(a.security_id, decision, target)}>{a.qualified_symbol}</Link><small>{a.company_name}</small></td>
        <td>{a.recommendation ? RECOMMENDATION[a.recommendation] : STATUS[a.status]}</td>
        <td>{signed(a.upside)}</td><td>{a.conviction ? `${cap(a.conviction)} (${a.conviction_points}/5)` : "—"}</td><td>{a.risk ? cap(a.risk) : "—"}</td>
        <td>{a.reasons.length > 0 ? a.reasons.join(" ") : a.score !== undefined ? `Score ${a.score.toFixed(3)}` : ""}</td></tr>)}
    </tbody></table></div>{more}
    <p><small>{ranking.method} Minimum upside {percent(ranking.rules.minimum_upside)}; Strong Buy needs {percent(ranking.rules.strong_upside)} upside, high conviction and low risk.</small></p>
  </section>;
}
