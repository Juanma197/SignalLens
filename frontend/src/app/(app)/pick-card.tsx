import Link from "next/link";
import {detailHref, percent} from "./view";
import type {ValueAssessment, Verdict, VerdictFigure, VerdictLevel, VerdictQuestion, Verdicts} from "./value-view";

export type Pick = ValueAssessment & {held: boolean; verdicts?: Verdicts|null; next_results_estimate?: string|null};

const QUESTION: Record<VerdictQuestion, string> = {cheap: "Price", quality: "Business", growth: "Growth", risk: "Risk"};
/** Words beside the colour, so the level never depends on colour alone. */
const LEVEL: Record<VerdictLevel, string> = {positive: "Good", mixed: "Mixed", negative: "Concern", neutral: "Info", unknown: "Unknown"};

export function figure(f: VerdictFigure) {
  switch (f.unit) {
    case "percent": return `${f.value > 0 && f.label.includes("vs") ? "+" : ""}${percent(f.value)}`;
    case "multiple": return `${f.value.toFixed(1)}×`;
    case "ratio": return f.value.toFixed(2);
    case "per_share_usd": return `$${f.value.toFixed(2)}`;
    case "usd": return Math.abs(f.value) >= 1e9 ? `$${(f.value / 1e9).toFixed(2)}B` : `$${(f.value / 1e6).toFixed(0)}M`;
    default: return String(f.value);
  }
}

function Detail({v, rule}: {v: Verdict; rule?: string}) {
  return <div className="pick-detail">
    <h4>{v.question === "action" ? "Ranking" : QUESTION[v.question]}</h4>
    {v.because.length > 0 && <ul>{v.because.map(b => <li key={b}>{b}</li>)}</ul>}
    {v.figures.length > 0 && <dl>{v.figures.map(f => <div key={f.label}><dt>{f.label}</dt><dd>{figure(f)}</dd></div>)}</dl>}
    {rule && <p><small>Rule: {rule}</small></p>}
  </div>;
}

/** One of the month's picks: the decision first, four plain answers, and the
 *  evidence one click away. Older snapshots have no verdicts and get the
 *  ranking's own figures instead. */
export function PickCard({p, decision, target, rules}: {p: Pick; decision: string; target: number; rules?: Record<string, string>|null}) {
  const answers = p.verdicts?.answers ?? [];
  const risk = answers.find(a => a.question === "risk");
  return <article className={`pick-card${p.rank === 1 ? " pick-card-first" : ""}`}>
    <header>
      <span className="pick-rank">#{p.rank}</span>
      <div><h3><Link href={detailHref(p.security_id, decision, target)}>{p.qualified_symbol}</Link></h3><p>{p.company_name}{p.held ? " · already held" : ""}</p></div>
      <span className="prototype-decision prototype-decision-buy-more">NEW PICK</span>
    </header>
    {p.verdicts ? <>
      <p className="pick-headline">{p.verdicts.action.headline}</p>
      <ul className="verdicts">{answers.map(a => <li key={a.question} className={`verdict verdict-${a.level}`}>
        <span className="verdict-tag">{QUESTION[a.question as VerdictQuestion]} · {LEVEL[a.level]}</span><span>{a.headline}</span></li>)}</ul>
      <p className="pick-meta">{risk && risk.level !== "positive" ? <>Main risk: {risk.because[0]}</> : "No warning signs found in the figures or filings."}
        {p.next_results_estimate && (p.next_results_estimate < decision.slice(0, 10)
          ? <> · Results were expected around {p.next_results_estimate}; none are filed yet at this cutoff</>
          : <> · Next results ≈ {p.next_results_estimate}</>)}</p>
      <details className="pick-details"><summary>Show figures and rules</summary>
        {answers.map(a => <Detail key={a.question} v={a} rule={rules?.[a.question]}/>)}
        <Detail v={p.verdicts.action} rule={rules?.action}/>
      </details>
    </> : <p className="pick-headline">Estimated upside {p.upside == null ? "—" : `${p.upside > 0 ? "+" : ""}${percent(p.upside)}`} · conviction {p.conviction} · risk {p.risk}</p>}
  </article>;
}
