"use client";
import Link from "next/link";
import {useEffect,useState} from "react";

type Vintage={vintage_id:string;decision_at:string;selection_count:number;selections:{rank:number;qualified_symbol:string;research_brief_href:string}[]};
type Data={vintages:{vintages:Vintage[];count:number};ledger:{official_vintage_count:number;immature_vintage_count:number;mature_126_session_count:number;mature_252_session_count:number;gate_state:string;configuration_hash:string};mtm:{vintages:{vintage_id:string;completed_session_count:number;checkpoints:{horizon_sessions:string|number;maturity_state:string;data_completeness_state:string;selected_portfolio_return?:number|null;validation_credit_state:string}[]}[]}};
const HASH="7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd";

export default function ValidationPage(){
 const [data,setData]=useState<Data|null>(null);const [failed,setFailed]=useState(false);
 useEffect(()=>{let live=true;Promise.all(["vintages","ledger","mark-to-market"].map(x=>fetch(`/api/research/validation/${x}`,{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error();return r.json()}))).then(([vintages,ledger,mtm])=>{if(live)setData({vintages,ledger,mtm})}).catch(()=>{if(live)setFailed(true)});return()=>{live=false}},[]);
 const ledger=data?.ledger;
 return <main className="validation-page"><nav><span className="mark">SL</span><strong>Prospective Validation</strong><Link href="/research">Company research</Link></nav>
 <section className="hero compact"><p className="eyebrow">PAPER RESEARCH ONLY · NO BROKER ACTIVITY</p><h1>Prospective evidence,<br/><span>without premature claims.</span></h1><p className="lede">INTERIM RETURNS ARE NOT VALIDATION. No recommendations, allocations, trades, or broker activity.</p></section>
 <section className="validation-alert">INSUFFICIENT PROSPECTIVE EVIDENCE</section>
 <div className="validation-grid"><section className="panel validation-card"><p className="eyebrow">FROZEN MODEL</p><h2>prospective-us-dilution-1.0.0</h2><p>Registered 2026-10-01T00:00:00Z · 90% price percentile + 10% dilution percentile · maximum 3</p><code>{ledger?.configuration_hash??HASH}</code></section>
 <section className="panel validation-card"><p className="eyebrow">NEXT PERMISSIBLE CYCLE</p><h2>Completed US month-end only</h2><p>First permissible: October 2026 month-end. Readiness requires exact session, prices, FX, SEC mapping/checkpoints, and bounded dilution evidence. Creation remains a separate operator-authorized CLI action.</p></section></div>
 {failed&&<p className="notice warning">Read-only validation data is unavailable. No values were inferred.</p>}
 <section className="panel validation-card"><p className="eyebrow">VALIDATION LEDGER</p><div className="score-grid"><div><span>Official vintages</span><b>{ledger?.official_vintage_count??"—"}</b></div><div><span>Immature</span><b>{ledger?.immature_vintage_count??"—"}</b></div><div><span>126 / 252 mature</span><b>{ledger?`${ledger.mature_126_session_count} / ${ledger.mature_252_session_count}`:"—"}</b></div></div><p>{ledger?.gate_state??"INSUFFICIENT PROSPECTIVE EVIDENCE"}</p></section>
 <section className="panel validation-card"><p className="eyebrow">OFFICIAL PAPER VINTAGES · TOP-3 AND COMPARATORS</p>{!data?.vintages.count?<p className="empty">No official vintage exists. Nothing has been backfilled.</p>:data.vintages.vintages.map(v=><article className="validation-vintage" key={v.vintage_id}><h2>{v.vintage_id}</h2><p>{v.decision_at} · {v.selection_count} selections · price-only Top 3 · equal-weight eligible universe</p><ol>{v.selections.map(s=><li key={s.qualified_symbol}>#{s.rank} <Link href={s.research_brief_href}>{s.qualified_symbol} research brief</Link></li>)}</ol></article>)}</section>
 <section className="panel validation-card"><p className="eyebrow">MARK TO MARKET · DESCRIPTIVE ONLY</p>{!data?.mtm.vintages.length?<p className="empty">No paper performance exists.</p>:data.mtm.vintages.map(v=><div key={v.vintage_id}><h2>{v.vintage_id}</h2><p>{v.completed_session_count} completed sessions</p><div className="checkpoint-grid">{v.checkpoints.map(c=><div key={c.horizon_sessions}><b>{c.horizon_sessions} sessions</b><span>{c.maturity_state} · {c.data_completeness_state}</span><strong>{c.selected_portfolio_return==null?"withheld":`${(c.selected_portfolio_return*100).toFixed(2)}%`}</strong><small>{c.validation_credit_state}</small></div>)}</div></div>)}</section>
 <footer>Database freshness and identity are checked on every report. Outputs are bounded and read only. PAPER RESEARCH ONLY · NO BROKER ACTIVITY.</footer></main>
}
