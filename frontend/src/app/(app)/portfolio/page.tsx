"use client";
import {Suspense, useCallback, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import {PrototypeNotice} from "../view";
import {cutoffFor, friendlyError} from "../when";
import {call} from "../store-view";
import {CashDraft, CashEntry, Portfolio, PortfolioView, SettingsDraft, TradeDraft} from "../portfolio-view";

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
    account_amount: d.currency === portfolio?.account_currency || !d.account_amount ? null : Number(d.account_amount), request_key: d.request_key});
  const cash = (d: CashDraft) => d.kind === "deposit" || d.kind === "withdrawal"
    ? run("portfolio/cash", {kind: d.kind, amount: Number(d.amount), moved_on: d.moved_on, note: d.note || null, request_key: d.request_key})
    : run("portfolio/adjustments", {kind: d.kind, amount: Number(d.amount), moved_on: d.moved_on, note: d.note || null, request_key: d.request_key,
        qualified_symbol: d.kind === "dividend" && d.qualified_symbol ? (d.qualified_symbol.includes(".") ? d.qualified_symbol : `${d.qualified_symbol}.US`).toUpperCase() : null});
  const settings = (d: SettingsDraft) => run("portfolio/settings", {monthly_contribution: Number(d.monthly_contribution), max_holdings: Number(d.max_holdings), fractional_shares: d.fractional_shares,
    position_limit: Number(d.position_limit) / 100, top3_limit: Number(d.top3_limit) / 100, minimum_trade: Number(d.minimum_trade)});
  const balance = (amount: number, asOf: string) => run("portfolio/broker-balance", {amount, as_of: asOf});
  function voidCash(entry: CashEntry) {
    const reason = window.prompt("Void this cash entry? It stays in the history marked as voided. Reason (optional):");
    if (reason === null) return;
    if (entry.adjustment_id) run("portfolio/adjustments/voids", {adjustment_id: entry.adjustment_id, reason: reason || null});
    else run("portfolio/cash/voids", {movement_id: entry.movement_id, reason: reason || null});
  }
  function voidTrade(id: string) {
    const reason = window.prompt("Void this trade? It stays in the history marked as voided. Reason (optional):");
    if (reason !== null) run("portfolio/voids", {transaction_id: id, reason: reason || null});
  }
  return <main className="research-page">
    <header className="dash-head"><div><h1>Portfolio</h1>
      <p>Built only from the trades, deposits, dividends and fees you record here. Market values use the latest stored close; holdings outside SignalLens data are never estimated.</p></div></header>
    <PrototypeNotice/>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {portfolio ? <PortfolioView portfolio={portfolio} cutoff={cutoff} today={today} busy={busy} onTrade={trade} onVoid={voidTrade} onCash={cash} onVoidCash={voidCash} onSettings={settings} onBalance={balance}/> : !error && <p role="status">Reading the prototype store…</p>}</main>;
}
export default function Page() {return <Suspense fallback={<p>Loading portfolio…</p>}><PortfolioPage/></Suspense>;}
