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
  fees: number; currency: string; traded_on: string; note: string|null; recorded_at: string; voided_at: string|null; void_reason: string|null; account_amount?: number|null};
export type CashEntry = {on: string; kind: "deposit"|"withdrawal"|"buy"|"sell"|"dividend"|"interest"|"fee"; amount: number; balance: number;
  movement_id?: string; transaction_id?: string; adjustment_id?: string; qualified_symbol?: string|null; note?: string|null};
export type CashPool = {currency: string; balance: number; overdrawn: boolean; deposited: number; withdrawn: number; spent_on_buys: number; received_from_sales: number;
  dividends?: number; interest?: number; fees?: number;
  deposited_this_month: number; uncounted_trades: {transaction_id: string; qualified_symbol: string; traded_on: string}[]; entries: CashEntry[]; method: string};
export type PortfolioSettings = {monthly_contribution: number; max_holdings: number; fractional_shares: boolean; currency: string; is_default: boolean; recorded_at: string|null;
  position_limit?: number; top3_limit?: number; minimum_trade?: number};
/** The cash pool checked against the balance your broker shows; a mismatch holds back buy suggestions. */
export type Reconciliation = {status: "unchecked"|"matched"|"mismatch"|"stale"; blocks_buys: boolean; message: string;
  broker_balance?: number; as_of?: string; app_balance?: number; difference?: number; tolerance?: number};
export type Portfolio = {as_of: string; method: string; positions: Position[]; closed_positions: Position[]; totals: Total[]; transactions: Transaction[]; currencies: string[];
  account_currency: string; cash: CashPool; settings: PortfolioSettings; reassessment?: "scheduled"|"telegram_not_configured";
  reconciliation?: Reconciliation; broker_balances?: {balance_id: string; amount: number; as_of: string; note: string|null}[];
  gbp_per_usd?: {rate: number; observed_on: string; source: string}|null};
/** A random id per submission, so a double click or a retry records once. */
export const requestKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

export function ReassessmentNotice({status}: {status?: Portfolio["reassessment"]}) {
  if (!status) return null;
  return <p role="status" className="notice">
    {status === "scheduled" ? "Reassessing your cash now. You'll get a Telegram message only if it has a worthwhile use; nothing is bought for you." :
      "Telegram isn't set up, so no message will follow."}{" "}See <Link href="/">This month</Link> for the suggested use of your cash.</p>;
}

export const money = (value: number, currency: string) =>
  new Intl.NumberFormat("en-US", {style: "currency", currency, maximumFractionDigits: 2}).format(value);
const signed = (value: number, currency: string) => (value > 0 ? "+" : "") + money(value, currency);
const shares = (value: number) => value.toLocaleString("en-US", {maximumFractionDigits: 6});
const name = (p: {company_name: string|null; listed_name?: string|null}) => p.company_name ?? p.listed_name ?? "";

/** Total value, available cash, invested market value and positions, in pounds. Holdings in
 *  dollars use the stored rate; with no rate, or an unpriced holding, the total says so. */
export function PortfolioSummary({portfolio}: {portfolio: Portfolio}) {
  const rate = portfolio.gbp_per_usd?.rate ?? null;
  const toGbp = (v: number, currency: string) => currency === "GBP" ? v : currency === "USD" && rate ? v * rate : null;
  const priced = portfolio.totals.filter(t => t.market_value != null).map(t => toGbp(t.market_value!, t.currency));
  const invested = priced.every(v => v != null) ? priced.reduce((a: number, v) => a + (v ?? 0), 0) : null;
  const unpriced = portfolio.totals.reduce((n, t) => n + t.positions - t.priced_positions, 0);
  const cash = portfolio.cash.balance;
  const result = portfolio.totals.map(t => t.unrealised_profit == null ? null : toGbp(t.unrealised_profit, t.currency));
  const profit = result.every(v => v != null) ? result.reduce((a: number, v) => a + (v ?? 0), 0) : null;
  const pounds = (v: number|null) => v == null ? "—" : money(v, "GBP");
  return <section className="stat-grid">
    <div className="stat-card"><div className="stat-label">Total portfolio value</div><b className="stat-value">{pounds(invested == null ? null : invested + cash)}</b>
      <small>{unpriced ? `${unpriced} unpriced holding${unpriced === 1 ? "" : "s"} excluded, not estimated` : "Holdings + cash"}</small></div>
    <div className="stat-card"><div className="stat-label">Available cash</div><b className="stat-value">{pounds(cash)}</b>
      <small>{portfolio.reconciliation?.status === "matched" ? "Matches your broker" : "Confirmed deposits, trades, dividends and fees"}</small></div>
    <div className="stat-card"><div className="stat-label">Invested market value</div>
      <b className="stat-value">{invested != null ? pounds(invested) : portfolio.totals.filter(t => t.market_value != null).map(t => money(t.market_value!, t.currency)).join(" + ") || "—"}</b>
      {invested == null && portfolio.totals.some(t => t.market_value != null) && <small>No stored £/$ rate, so not converted to pounds</small>}
      {profit != null && <small className={profit >= 0 ? "gain" : "loss"}>{profit >= 0 ? "+" : "−"}{pounds(Math.abs(profit))} unrealised</small>}
      {rate && <small>$ at {rate.toFixed(4)} £/$ ({portfolio.gbp_per_usd!.observed_on})</small>}</div>
    <div className="stat-card"><div className="stat-label">Portfolio positions</div><b className="stat-value">{portfolio.positions.length} / {portfolio.settings.max_holdings}</b>
      <small>A maximum, not a target</small></div>
  </section>;
}

/** Your broker's cash balance against the cash pool. */
export function ReconcilePanel({portfolio, today, busy, onBalance}: {portfolio: Portfolio; today: string; busy: boolean;
  onBalance: (amount: number, asOf: string) => Promise<boolean>}) {
  const check = portfolio.reconciliation;
  const [amount, setAmount] = useState("");
  const [asOf, setAsOf] = useState(today);
  const tone = !check ? "" : check.status === "mismatch" ? " warning" : check.status === "matched" ? " matched" : "";
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onBalance(Number(amount), asOf)) setAmount("");
  }
  return <section id="reconcile" className="panel prototype-panel"><h2>Check against your broker</h2>
    {check && <p className={`notice${tone}`}>{check.message}{check.difference != null && check.status !== "matched" &&
      <> Difference {signed(check.difference, "GBP")} (allowed ± {money(check.tolerance ?? 0, "GBP")}).</>}</p>}
    <p>Enter the cash your broker shows. SignalLens holds back buy suggestions while its cash pool and your broker disagree, so it never suggests spending money that may not be there.</p>
    <form onSubmit={submit} className="prototype-trade-form">
      <label>Broker cash balance (£)<input required type="number" min="0" step="any" value={amount} onChange={e => setAmount(e.target.value)}/></label>
      <label>As of<input required type="date" max={today} value={asOf} onChange={e => setAsOf(e.target.value)}/></label>
      <button disabled={busy}>{busy ? "Saving…" : "Record balance"}</button>
    </form></section>;
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

export type TradeDraft = {kind: "buy"|"sell"; qualified_symbol: string; shares: string; price: string; fees: string; currency: string; traded_on: string; company_name: string; note: string; account_amount: string; request_key: string};
export const emptyTrade = (today: string): TradeDraft => ({kind: "buy", qualified_symbol: "", shares: "", price: "", fees: "0", currency: "USD", traded_on: today, company_name: "", note: "", account_amount: "", request_key: requestKey()});

export function TradeForm({currencies, account, today, busy, onSubmit}: {currencies: string[]; account: string; today: string; busy: boolean; onSubmit: (draft: TradeDraft) => Promise<boolean>}) {
  const [draft, setDraft] = useState<TradeDraft>(emptyTrade(today));
  const set = (key: keyof TradeDraft) => (e: {target: {value: string}}) => setDraft(d => ({...d, [key]: e.target.value}));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onSubmit(draft)) setDraft(d => ({...emptyTrade(today), currency: d.currency, traded_on: d.traded_on}));
  }
  return <section id="record-trade" className="panel prototype-panel"><h2>Record a trade</h2>
    <p>Enter trades you have already made with your broker. SignalLens never places orders. A ticker without a suffix is treated as US (AAPL becomes AAPL.US).
      For a trade in another currency, enter the {account} total your broker charged or paid, including fees and conversion, so the cash pool matches your account.</p>
    <form onSubmit={submit} className="prototype-trade-form">
      <label>Type<select value={draft.kind} onChange={set("kind")}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      <label>Ticker<input required maxLength={32} value={draft.qualified_symbol} onChange={set("qualified_symbol")} placeholder="AAPL"/></label>
      <label>Shares<input required type="number" min="0" step="any" value={draft.shares} onChange={set("shares")}/></label>
      <label>Price per share<input required type="number" min="0" step="any" value={draft.price} onChange={set("price")}/></label>
      <label>Fees<input type="number" min="0" step="any" value={draft.fees} onChange={set("fees")}/></label>
      <label>Currency<select value={draft.currency} onChange={set("currency")}>{currencies.map(c => <option key={c}>{c}</option>)}</select></label>
      {draft.currency !== account && <label>{draft.kind === "buy" ? "Total paid" : "Total received"} in {account}<input required type="number" min="0" step="any" value={draft.account_amount} onChange={set("account_amount")} placeholder="From your broker's confirmation"/></label>}
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
        <td>{money(t.shares * t.price, t.currency)}{t.account_amount != null && t.currency !== "GBP" && <small>{money(t.account_amount, "GBP")}</small>}</td>
        <td>{t.voided_at ? <small>voided{t.void_reason ? `: ${t.void_reason}` : ""}</small> : <button disabled={busy} onClick={() => onVoid(t.transaction_id)}>Void</button>}</td>
      </tr>)}</tbody></table></div></section>;
}

export type CashDraft = {kind: "deposit"|"withdrawal"|"dividend"|"interest"|"fee"; amount: string; moved_on: string; note: string; qualified_symbol: string; request_key: string};
const CASH_KIND = {deposit: "Deposit", withdrawal: "Withdrawal", buy: "Buy", sell: "Sale", dividend: "Dividend", interest: "Interest", fee: "Fee"};

export function CashPanel({cash, settings, holdings, today, busy, onCash, onVoid}: {cash: CashPool; settings: PortfolioSettings; holdings: number; today: string; busy: boolean;
  onCash: (draft: CashDraft) => Promise<boolean>; onVoid: (entry: CashEntry) => void}) {
  const pounds = (v: number) => money(v, cash.currency);
  const [draft, setDraft] = useState<CashDraft>({kind: "deposit", amount: String(settings.monthly_contribution), moved_on: today, note: "", qualified_symbol: "", request_key: requestKey()});
  const set = (key: keyof CashDraft) => (e: {target: {value: string}}) => setDraft(d => ({...d, [key]: e.target.value}));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onCash(draft)) setDraft(d => ({...d, amount: d.kind === "deposit" ? String(settings.monthly_contribution) : "", note: "", qualified_symbol: "", request_key: requestKey()}));
  }
  const uncounted = cash.uncounted_trades.length;
  return <section className="panel prototype-panel"><h2>Cash pool</h2>
    <section className="prototype-cards">
      <article><p className="eyebrow">Available cash · {cash.currency}</p><h3>{pounds(cash.balance)}</h3>
        <p>Carries over from month to month. Sale proceeds come back here.</p></article>
      <article><p className="eyebrow">This month&apos;s deposit</p><h3>{pounds(cash.deposited_this_month)}</h3>
        <p>Planned {pounds(settings.monthly_contribution)}. A deposit, not a budget: what you don&apos;t invest carries over.</p></article>
      <article><p className="eyebrow">Dividends and fees</p><h3>{pounds((cash.dividends ?? 0) + (cash.interest ?? 0) - (cash.fees ?? 0))}</h3>
        <p>{pounds(cash.dividends ?? 0)} dividends, {pounds(cash.interest ?? 0)} interest, {pounds(cash.fees ?? 0)} fees. {holdings} of {settings.max_holdings} holdings.</p></article>
    </section>
    {cash.overdrawn && <p className="notice warning">The cash pool is below zero, so a deposit is probably missing. Record it so the balance matches your broker.</p>}
    {uncounted > 0 && <p className="notice warning">{uncounted} earlier trade{uncounted === 1 ? " has" : "s have"} no {cash.currency} total and {uncounted === 1 ? "is" : "are"} not counted in the cash pool.
      If they were recorded before cash tracking, record your broker&apos;s current cash as one deposit instead.</p>}
    <form onSubmit={submit} className="prototype-trade-form">
      <label>Type<select value={draft.kind} onChange={set("kind")}><option value="deposit">Deposit (money arrived)</option><option value="withdrawal">Withdrawal</option>
        <option value="dividend">Dividend received</option><option value="interest">Interest received</option><option value="fee">Fee charged</option></select></label>
      <label>Amount ({cash.currency})<input required type="number" min="0" step="any" value={draft.amount} onChange={set("amount")}/></label>
      <label>Date<input required type="date" max={today} value={draft.moved_on} onChange={set("moved_on")}/></label>
      {draft.kind === "dividend" && <label>From (ticker, optional)<input maxLength={32} value={draft.qualified_symbol} onChange={set("qualified_symbol")} placeholder="WU"/></label>}
      <label>Note (optional)<input maxLength={4000} value={draft.note} onChange={set("note")} placeholder={draft.kind === "deposit" ? `Contribution ${today.slice(0, 7)}` : ""}/></label>
      <button disabled={busy}>{busy ? "Saving…" : `Record ${draft.kind}`}</button>
    </form>
    {cash.entries.length > 0 && <div className="prototype-table-wrap"><table><thead><tr><th>Date</th><th>Movement</th><th>Amount</th><th>Balance</th><th></th></tr></thead><tbody>
      {cash.entries.slice(0, 30).map(e => <tr key={`${e.kind}-${e.movement_id ?? e.adjustment_id ?? e.transaction_id}`}>
        <td>{e.on}</td>
        <td><b>{CASH_KIND[e.kind]}{e.qualified_symbol ? ` ${e.qualified_symbol}` : ""}</b>{e.note && <small>{e.note}</small>}</td>
        <td>{signed(e.amount, cash.currency)}</td><td>{pounds(e.balance)}</td>
        <td>{(e.movement_id || e.adjustment_id) && <button disabled={busy} onClick={() => onVoid(e)}>Void</button>}</td>
      </tr>)}</tbody></table></div>}
    <p className="prototype-method"><small>{cash.method} Trades are voided in the trade history.</small></p>
  </section>;
}

export type SettingsDraft = {monthly_contribution: string; max_holdings: string; fractional_shares: boolean; position_limit: string; top3_limit: string; minimum_trade: string};
const pct = (v: number|undefined, fallback: number) => String(Math.round((v ?? fallback) * 100));

export function SettingsPanel({settings, busy, onSave}: {settings: PortfolioSettings; busy: boolean; onSave: (draft: SettingsDraft) => Promise<boolean>}) {
  const [draft, setDraft] = useState<SettingsDraft>({monthly_contribution: String(settings.monthly_contribution), max_holdings: String(settings.max_holdings),
    fractional_shares: settings.fractional_shares, position_limit: pct(settings.position_limit, 0.15), top3_limit: pct(settings.top3_limit, 0.4),
    minimum_trade: String(settings.minimum_trade ?? 25)});
  const set = (key: keyof SettingsDraft) => (e: {target: {value: string}}) => setDraft(d => ({...d, [key]: e.target.value}));
  return <section id="plan" className="panel prototype-panel"><h2>Your plan</h2>
    <p>Change these whenever your plans change; earlier values are kept.{settings.is_default ? " These are the starting defaults." : ""} Limits are shares of the whole
      portfolio&apos;s market value. A holding that grows past its limit is flagged for review; nothing is sold just for that.</p>
    <form onSubmit={e => {e.preventDefault(); onSave(draft);}} className="prototype-trade-form">
      <label>Monthly contribution ({settings.currency})<input required type="number" min="0" step="any" value={draft.monthly_contribution} onChange={set("monthly_contribution")}/></label>
      <label>Maximum holdings<input required type="number" min="1" max="30" step="1" value={draft.max_holdings} onChange={set("max_holdings")}/></label>
      <label>Maximum in one stock (%)<input required type="number" min="5" max="100" step="1" value={draft.position_limit} onChange={set("position_limit")}/></label>
      <label>Maximum in the three largest (%)<input required type="number" min="5" max="100" step="1" value={draft.top3_limit} onChange={set("top3_limit")}/></label>
      <label>Minimum trade ({settings.currency})<input required type="number" min="0" step="any" value={draft.minimum_trade} onChange={set("minimum_trade")}/></label>
      <label className="prototype-inline-check"><input type="checkbox" checked={draft.fractional_shares} onChange={e => setDraft(d => ({...d, fractional_shares: e.target.checked}))}/> My broker lets me buy part of a share (Trading 212 does)</label>
      <button disabled={busy}>{busy ? "Saving…" : "Save plan"}</button>
    </form></section>;
}

export function PortfolioView({portfolio, cutoff, today, busy, onTrade, onVoid, onCash, onVoidCash, onSettings, onBalance}: {portfolio: Portfolio; cutoff: string; today: string; busy: boolean;
  onTrade: (draft: TradeDraft) => Promise<boolean>; onVoid: (id: string) => void; onCash: (draft: CashDraft) => Promise<boolean>;
  onVoidCash: (entry: CashEntry) => void; onSettings: (draft: SettingsDraft) => Promise<boolean>; onBalance?: (amount: number, asOf: string) => Promise<boolean>}) {
  return <div className="brief-stack">
    <ReassessmentNotice status={portfolio.reassessment}/>
    <PortfolioSummary portfolio={portfolio}/>
    {portfolio.reconciliation?.blocks_buys && <p className="notice warning">{portfolio.reconciliation.message}</p>}
    <CashPanel key={`cash-${portfolio.settings.recorded_at}`} cash={portfolio.cash} settings={portfolio.settings} holdings={portfolio.positions.length} today={today} busy={busy} onCash={onCash} onVoid={onVoidCash}/>
    <HoldingsTable positions={portfolio.positions} cutoff={cutoff}/>
    <TradeForm currencies={portfolio.currencies} account={portfolio.account_currency} today={today} busy={busy} onSubmit={onTrade}/>
    <TransactionHistory transactions={portfolio.transactions} busy={busy} onVoid={onVoid}/>
    <ClosedPositions positions={portfolio.closed_positions}/>
    {onBalance && <ReconcilePanel portfolio={portfolio} today={today} busy={busy} onBalance={onBalance}/>}
    <SettingsPanel key={`plan-${portfolio.settings.recorded_at}`} settings={portfolio.settings} busy={busy} onSave={onSettings}/>
    <p className="prototype-method"><small>Method: {portfolio.method}. Valued {portfolio.as_of.replace("T", " ").slice(0, 16)} UTC.</small></p>
  </div>;
}
