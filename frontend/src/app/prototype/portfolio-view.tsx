"use client";
import Link from "next/link";
import {FormEvent, useState} from "react";
import {detailHref, percent} from "./view";

export type Position = {qualified_symbol: string; currency: string; company_name: string|null; listed_name?: string|null; security_id?: string|null;
  shares: number; cost_basis: number; average_cost: number|null; realised_profit: number; fees: number; first_traded_on: string; last_traded_on: string; trades: number;
  price?: {close: number; trading_date: string}|null; market_value?: number|null; unrealised_profit?: number|null; unrealised_return?: number|null; weight?: number|null};
export type Total = {currency: string; positions: number; priced_positions: number; cost_basis: number; priced_cost_basis: number;
  market_value: number|null; unrealised_profit: number|null; unrealised_return: number|null; realised_profit: number};
export type Transaction = {transaction_id: string; kind: "buy"|"sell"; qualified_symbol: string; company_name: string|null; shares: number; price: number;
  fees: number; currency: string; traded_on: string; note: string|null; recorded_at: string; voided_at: string|null; void_reason: string|null};
export type Portfolio = {as_of: string; method: string; positions: Position[]; closed_positions: Position[]; totals: Total[]; transactions: Transaction[]; currencies: string[]};

export const money = (value: number, currency: string) =>
  new Intl.NumberFormat("en-US", {style: "currency", currency, maximumFractionDigits: 2}).format(value);
const signed = (value: number, currency: string) => (value > 0 ? "+" : "") + money(value, currency);
const shares = (value: number) => value.toLocaleString("en-US", {maximumFractionDigits: 6});
const name = (p: {company_name: string|null; listed_name?: string|null}) => p.company_name ?? p.listed_name ?? "";

export function PortfolioSummary({totals}: {totals: Total[]}) {
  if (totals.length === 0) return null;
  return <>{totals.map(t => <section className="prototype-cards panel" key={t.currency}>
    <article><p className="eyebrow">Market value · {t.currency}</p><h3>{t.market_value === null ? "No stored prices" : money(t.market_value, t.currency)}</h3>
      <p>{t.priced_positions} of {t.positions} holdings priced{t.priced_positions < t.positions ? "; unpriced holdings are excluded, not estimated" : ""}.</p></article>
    <article><p className="eyebrow">Unrealised result</p><h3>{t.unrealised_profit === null ? "—" : signed(t.unrealised_profit, t.currency)}</h3>
      <p>{t.unrealised_return === null ? "Needs a stored price." : `${percent(t.unrealised_return)} on ${money(t.priced_cost_basis, t.currency)} priced cost.`}</p></article>
    <article><p className="eyebrow">Invested and realised</p><h3>{money(t.cost_basis, t.currency)}</h3>
      <p>Cost of open holdings. Realised from sales: {signed(t.realised_profit, t.currency)}.</p></article>
  </section>)}</>;
}

export function HoldingsTable({positions, cutoff}: {positions: Position[]; cutoff: string}) {
  if (positions.length === 0) return <section className="panel prototype-panel"><h2>Holdings</h2><p>No open holdings. Record a buy below.</p></section>;
  return <section className="panel prototype-panel"><h2>Holdings</h2>
    <div className="prototype-table-wrap"><table><thead><tr><th>Holding</th><th>Shares</th><th>Average cost</th><th>Last stored close</th><th>Market value</th><th>Unrealised</th><th>Weight</th></tr></thead><tbody>
      {positions.map(p => <tr key={p.qualified_symbol + p.currency}>
        <td><b>{p.security_id && cutoff ? <Link href={detailHref(p.security_id, cutoff)}>{p.qualified_symbol}</Link> : p.qualified_symbol}</b><small>{name(p)}</small></td>
        <td>{shares(p.shares)}</td>
        <td>{p.average_cost === null ? "—" : money(p.average_cost, p.currency)}<small>cost {money(p.cost_basis, p.currency)}</small></td>
        <td>{p.price ? money(p.price.close, p.currency) : "no stored price"}<small>{p.price ? p.price.trading_date : "not covered by SignalLens data"}</small></td>
        <td>{p.market_value == null ? "—" : money(p.market_value, p.currency)}</td>
        <td>{p.unrealised_profit == null ? "—" : signed(p.unrealised_profit, p.currency)}<small>{p.unrealised_return == null ? "" : percent(p.unrealised_return)}</small></td>
        <td>{p.weight == null ? "—" : percent(p.weight)}</td>
      </tr>)}</tbody></table></div></section>;
}

export function ClosedPositions({positions}: {positions: Position[]}) {
  if (positions.length === 0) return null;
  return <section className="panel prototype-panel"><h2>Closed positions</h2><div className="prototype-table-wrap"><table><thead><tr><th>Holding</th><th>Held</th><th>Realised result</th></tr></thead><tbody>
    {positions.map(p => <tr key={p.qualified_symbol + p.currency}><td><b>{p.qualified_symbol}</b><small>{name(p)}</small></td><td>{p.first_traded_on} to {p.last_traded_on}<small>{p.trades} trades</small></td><td>{signed(p.realised_profit, p.currency)}<small>after {money(p.fees, p.currency)} fees</small></td></tr>)}
  </tbody></table></div></section>;
}

export type TradeDraft = {kind: "buy"|"sell"; qualified_symbol: string; shares: string; price: string; fees: string; currency: string; traded_on: string; company_name: string; note: string};
export const emptyTrade = (today: string): TradeDraft => ({kind: "buy", qualified_symbol: "", shares: "", price: "", fees: "0", currency: "USD", traded_on: today, company_name: "", note: ""});

export function TradeForm({currencies, today, busy, onSubmit}: {currencies: string[]; today: string; busy: boolean; onSubmit: (draft: TradeDraft) => Promise<boolean>}) {
  const [draft, setDraft] = useState<TradeDraft>(emptyTrade(today));
  const set = (key: keyof TradeDraft) => (e: {target: {value: string}}) => setDraft(d => ({...d, [key]: e.target.value}));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onSubmit(draft)) setDraft(d => ({...emptyTrade(today), currency: d.currency, traded_on: d.traded_on}));
  }
  return <section className="panel prototype-panel"><h2>Record a trade</h2>
    <p>Enter trades you have already made with your broker. SignalLens never places orders. A ticker without a suffix is treated as US (AAPL becomes AAPL.US).</p>
    <form onSubmit={submit} className="prototype-trade-form">
      <label>Type<select value={draft.kind} onChange={set("kind")}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      <label>Ticker<input required maxLength={32} value={draft.qualified_symbol} onChange={set("qualified_symbol")} placeholder="AAPL"/></label>
      <label>Shares<input required type="number" min="0" step="any" value={draft.shares} onChange={set("shares")}/></label>
      <label>Price per share<input required type="number" min="0" step="any" value={draft.price} onChange={set("price")}/></label>
      <label>Fees<input type="number" min="0" step="any" value={draft.fees} onChange={set("fees")}/></label>
      <label>Currency<select value={draft.currency} onChange={set("currency")}>{currencies.map(c => <option key={c}>{c}</option>)}</select></label>
      <label>Trade date<input required type="date" max={today} value={draft.traded_on} onChange={set("traded_on")}/></label>
      <label>Company name (optional)<input maxLength={256} value={draft.company_name} onChange={set("company_name")}/></label>
      <label className="wide">Why (optional)<textarea maxLength={4000} rows={2} value={draft.note} onChange={set("note")} placeholder="The reason for this trade, for later review."/></label>
      <button disabled={busy}>{busy ? "Saving…" : `Record ${draft.kind}`}</button>
    </form></section>;
}

export function TransactionHistory({transactions, busy, onVoid}: {transactions: Transaction[]; busy: boolean; onVoid: (id: string) => void}) {
  if (transactions.length === 0) return null;
  return <section className="panel prototype-panel"><h2>Trade history</h2>
    <p>Trades cannot be edited or deleted. Void a mistaken trade and record the correct one; the voided row stays visible.</p>
    <div className="prototype-table-wrap"><table><thead><tr><th>Date</th><th>Trade</th><th>Price</th><th>Total</th><th></th></tr></thead><tbody>
      {transactions.map(t => <tr key={t.transaction_id} className={t.voided_at ? "prototype-voided" : undefined}>
        <td>{t.traded_on}</td>
        <td><b>{t.kind === "buy" ? "Buy" : "Sell"} {shares(t.shares)} {t.qualified_symbol}</b>{t.note && <small>{t.note}</small>}</td>
        <td>{money(t.price, t.currency)}{t.fees > 0 && <small>fees {money(t.fees, t.currency)}</small>}</td>
        <td>{money(t.shares * t.price, t.currency)}</td>
        <td>{t.voided_at ? <small>voided{t.void_reason ? `: ${t.void_reason}` : ""}</small> : <button disabled={busy} onClick={() => onVoid(t.transaction_id)}>Void</button>}</td>
      </tr>)}</tbody></table></div></section>;
}

export function PortfolioView({portfolio, cutoff, today, busy, onTrade, onVoid}: {portfolio: Portfolio; cutoff: string; today: string; busy: boolean;
  onTrade: (draft: TradeDraft) => Promise<boolean>; onVoid: (id: string) => void}) {
  return <div className="brief-stack">
    <PortfolioSummary totals={portfolio.totals}/>
    <HoldingsTable positions={portfolio.positions} cutoff={cutoff}/>
    <TradeForm currencies={portfolio.currencies} today={today} busy={busy} onSubmit={onTrade}/>
    <TransactionHistory transactions={portfolio.transactions} busy={busy} onVoid={onVoid}/>
    <ClosedPositions positions={portfolio.closed_positions}/>
    <p className="prototype-method"><small>Method: {portfolio.method}. Valued {portfolio.as_of.replace("T", " ").slice(0, 16)} UTC.</small></p>
  </div>;
}
