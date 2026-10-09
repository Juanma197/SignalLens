"use client";
import {Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {call} from "../store-view";
import {Portfolio, PortfolioView, TradeDraft} from "../portfolio-view";

function PortfolioPage() {
  const query = useSearchParams();
  const [cutoff, setCutoff] = useState(query.get("decision_at") ?? "");
  const [portfolio, setPortfolio] = useState<Portfolio|null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const load = useCallback(() => call<Portfolio>("portfolio").then(setPortfolio).catch(e => setError(e.message)), []);
  useEffect(() => {load();}, [load]);
  async function run(path: string, body: unknown) {
    setBusy(true); setError("");
    try {setPortfolio(await call<Portfolio>(path, body)); return true;}
    catch (e) {setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"); return false;}
    finally {setBusy(false);}
  }
  const trade = (d: TradeDraft) => run("portfolio/trades", {kind: d.kind, qualified_symbol: d.qualified_symbol, shares: Number(d.shares), price: Number(d.price),
    fees: Number(d.fees || 0), currency: d.currency, traded_on: d.traded_on, company_name: d.company_name || null, note: d.note || null});
  function voidTrade(id: string) {
    const reason = window.prompt("Void this trade? It stays in the history marked as voided. Reason (optional):");
    if (reason !== null) run("portfolio/voids", {transaction_id: id, reason: reason || null});
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Portfolio</strong><Link href="/prototype">Shortlist</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">YOUR HOLDINGS · YOUR TRADES · STORED PRICES</p><h1>What you own.<br/><span>What it is worth.</span></h1>
      <p className="lede">Holdings are built only from trades you record here. Market values use the latest stored close; holdings outside SignalLens data are shown at cost and never estimated.</p></section>
    <PrototypeNotice/>
    <form className="panel research-form" onSubmit={e => e.preventDefault()}><label>Evidence cutoff for company links (optional)<input type="text" placeholder="2026-10-02T12:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)}/></label></form>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {portfolio ? <PortfolioView portfolio={portfolio} cutoff={cutoff} today={today} busy={busy} onTrade={trade} onVoid={voidTrade}/> : !error && <p role="status">Reading the prototype store…</p>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading portfolio…</p>}><PortfolioPage/></Suspense>;}
