"use client";
import { FormEvent, useState } from "react";
import Link from "next/link";

type Item = {metric: string; classification: string; direction: string};
type Event = {publication_timestamp: string; form: string; category: string; amendment: boolean; explanation: string; citation: {accession: string}};
type Brief = {notice: string; known_at: string; company_identity: Record<string,string>;
  why_it_is_being_viewed: Record<string,unknown>; price_behaviour: Record<string,unknown>;
  dilution_share_count_evidence: Record<string,unknown>; financial_context: Item[];
  recent_official_filings_events: Event[]; risks_and_warnings: string[]; missing_information: string[]};

export default function ResearchPage() {
  const [symbol, setSymbol] = useState(""); const [decision, setDecision] = useState("");
  const [brief, setBrief] = useState<Brief|null>(null); const [error, setError] = useState("");
  async function load(event: FormEvent) {
    event.preventDefault(); setError(""); setBrief(null);
    const at = decision ? new Date(decision).toISOString() : "";
    const response = await fetch(`/api/research/company-brief?qualified_symbol=${encodeURIComponent(symbol.toUpperCase())}&decision_at=${encodeURIComponent(at)}`, {cache:"no-store"});
    if (!response.ok) { setError("The brief was safely withheld. Check the qualified symbol, timestamp, and model-ready evidence."); return; }
    setBrief(await response.json());
  }
  const pct = (v: unknown) => typeof v === "number" ? `${(v*100).toFixed(1)}%` : "Unavailable";
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Company research</strong><Link href="/">Research view</Link><Link href="/prototype">Unvalidated prototype</Link></nav>
    <section className="hero compact"><p className="eyebrow">EXPLAINABLE · POINT IN TIME · READ ONLY</p><h1>What was known,<br/><span>and why it mattered.</span></h1><p className="lede">Deterministic finance-language context from registered evidence. No generated claims or predictions.</p></section>
    <form className="panel research-form" onSubmit={load}><label>Exchange-qualified symbol<input required maxLength={40} placeholder="AAPL.US" value={symbol} onChange={e=>setSymbol(e.target.value)}/></label><label>Known-at timestamp (UTC)<input required type="datetime-local" value={decision} onChange={e=>setDecision(e.target.value)}/></label><button>View read-only brief</button></form>
    {error && <p className="notice warning">{error}</p>}
    {brief && <div className="brief-stack">
      <section className="panel"><header><div><p className="eyebrow">{brief.notice}</p><h2>{brief.company_identity.company_name} · {brief.company_identity.qualified_symbol}</h2></div><div className="date">Known at {new Date(brief.known_at).toLocaleString()}</div></header>
        <div className="score-grid"><div><span>Price peer percentile</span><b>{pct(brief.price_behaviour.price_percentile)}</b><small>{String(brief.price_behaviour.peer_strength)} relative to eligible US peers</small></div><div><span>Price contribution · 90%</span><b>{pct(brief.price_behaviour.price_contribution)}</b><small>Frozen score component</small></div><div><span>Dilution contribution · 10%</span><b>{pct(brief.dilution_share_count_evidence.frozen_score_contribution)}</b><small>{String(brief.dilution_share_count_evidence.ownership_effect)}</small></div></div>
        <div className="notice">{String(brief.why_it_is_being_viewed.reason)}</div></section>
      <section className="panel"><header><div><p className="eyebrow">CONTEXT ONLY — NO SCORE EFFECT</p><h2>Financial context</h2></div></header><div className="context-table">{brief.financial_context.map(x=><div key={x.metric}><b>{x.metric.replaceAll("_"," ")}</b><span className={x.classification}>{x.classification}</span><small>{x.direction}</small></div>)}</div></section>
      <section className="panel"><header><div><p className="eyebrow">OFFICIAL SEC METADATA</p><h2>Recent filing timeline</h2></div></header><div className="timeline">{brief.recent_official_filings_events.map(e=><div key={e.citation.accession}><time>{new Date(e.publication_timestamp).toLocaleString()}</time><b>{e.form}{e.amendment ? " · amendment" : ""}</b><span>{e.category}</span><p>{e.explanation}</p><code>{e.citation.accession}</code></div>)}</div></section>
      <section className="panel flags"><header><div><p className="eyebrow">DESCRIPTIVE — NOT AN INSTRUCTION</p><h2>Risks and missing information</h2></div></header><p>{brief.risks_and_warnings.join(" · ")}</p><p>Missing: {brief.missing_information.join(", ") || "none recorded"}</p></section>
    </div>}
  </main>;
}
