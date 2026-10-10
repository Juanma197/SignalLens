import {percent} from "./view";
import type {ValueAssessment, Verdict, VerdictFigure, VerdictLevel, VerdictQuestion, Verdicts} from "./value-view";

export type Pick = ValueAssessment & {held: boolean; verdicts?: Verdicts|null; next_results_estimate?: string|null};

export const QUESTION: Record<VerdictQuestion, string> = {cheap: "Price", quality: "Business", growth: "Growth", risk: "Risk"};
/** Words beside the colour, so the level never depends on colour alone. */
export const LEVEL: Record<VerdictLevel, string> = {positive: "Good", mixed: "Mixed", negative: "Concern", neutral: "Info", unknown: "Unknown"};

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

export function Detail({v, rule}: {v: Verdict; rule?: string}) {
  return <div className="pick-detail">
    <h4>{v.question === "action" ? "Ranking" : QUESTION[v.question]}</h4>
    {v.because.length > 0 && <ul>{v.because.map(b => <li key={b}>{b}</li>)}</ul>}
    {v.figures.length > 0 && <dl>{v.figures.map(f => <div key={f.label}><dt>{f.label}</dt><dd>{figure(f)}</dd></div>)}</dl>}
    {rule && <p><small>Rule: {rule}</small></p>}
  </div>;
}
