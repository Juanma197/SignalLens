"use client";
import {Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {cutoffFor, friendlyError} from "../when";
import {call} from "../store-view";
import {CashDraft, Portfolio, PortfolioView, SettingsDraft, TradeDraft} from "../portfolio-view";

function PortfolioPage() {
  const query = useSearchParams();
  // Company links open on the latest data unless the page was opened with a date.
  const [cutoff] = useState(() => query.get("decision_at") || cutoffFor({mode: "latest"}));
  const [portfolio, setPortfolio] = useState<Portfolio|null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const load = useCallback(() => call<Portfolio>("portfolio").then(setPortfolio).catch(e => setError(friendlyError(e.message))), []);
  useEffect(() => {load();}, [load]);
  async function run(path: string, body: unknown) {
    setBusy(true); setError("");
    try {setPortfolio(await call<Portfolio>(path, body)); return true;}
    catch (e) {setError(friendlyError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE")); return false;}
    finally {setBusy(false);}
  }
  const trade = (d: TradeDraft) => run("portfolio/trades", {kind: d.kind, qualified_symbol: d.qualified_symbol, shares: Number(d.shares), price: Number(d.price),
    fees: Number(d.fees || 0), currency: d.currency, traded_on: d.traded_on, company_name: d.company_name || null, note: d.note || null,
    account_amount: d.currency === portfolio?.account_currency || !d.account_amount ? null : Number(d.account_amount)});
  const cash = (d: CashDraft) => run("portfolio/cash", {kind: d.kind, amount: Number(d.amount), moved_on: d.moved_on, note: d.note || null});
  const settings = (d: SettingsDraft) => run("portfolio/settings", {monthly_contribution: Number(d.monthly_contribution), max_holdings: Number(d.max_holdings), fractional_shares: d.fractional_shares});
  function voidCash(id: string) {
    const reason = window.prompt("Void this cash movement? It stays in the history marked as voided. Reason (optional):");
    if (reason !== null) run("portfolio/cash/voids", {movement_id: id, reason: reason || null});
  }
  function voidTrade(id: string) {
    const reason = window.prompt("Void this trade? It stays in the history marked as voided. Reason (optional):");
    if (reason !== null) run("portfolio/voids", {transaction_id: id, reason: reason || null});
  }
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Portfolio</strong><Link href="/prototype/monthly">This month</Link><Link href="/prototype">Shortlist</Link><Link href="/prototype/checks">Thesis checks</Link><Link href="/prototype/watchlist">Watchlist</Link><Link href="/prototype/snapshots">Snapshots</Link></nav>
    <section className="hero compact"><p className="eyebrow">YOUR HOLDINGS · YOUR TRADES · STORED PRICES</p><h1>What you own.<br/><span>What it is worth.</span></h1>
      <p className="lede">Holdings and cash are built only from the trades and deposits you record here. Market values use the latest stored close; holdings outside SignalLens data are shown at cost and never estimated.</p></section>
    <PrototypeNotice/>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {portfolio ? <PortfolioView portfolio={portfolio} cutoff={cutoff} today={today} busy={busy} onTrade={trade} onVoid={voidTrade} onCash={cash} onVoidCash={voidCash} onSettings={settings}/> : !error && <p role="status">Reading the prototype store…</p>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading portfolio…</p>}><PortfolioPage/></Suspense>;}
