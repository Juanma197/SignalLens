import Link from "next/link";
import type {ReactNode} from "react";
import {OVERALL, type CompanyChecks} from "./checks-view";
import {money, type Reconciliation, type Total} from "./portfolio-view";
import {detailHref, percent} from "./view";
import {Detail, LEVEL, QUESTION, type Pick} from "./pick-card";
import type {VerdictQuestion, Verdicts} from "./value-view";

export type Decision = "SELL"|"REDUCE"|"REVIEW"|"BUY MORE"|"HOLD";
export type Holding = {qualified_symbol: string; security_id: string|null; company_name: string|null; currency: string; shares: number;
  average_cost: number|null; cost_basis: number; price: {close: number; trading_date: string}|null; market_value: number|null;
  unrealised_return: number|null; weight: number|null; checks: CompanyChecks|null; decision: Decision; reasons: string[];
  evidence: {upside: number|null; weight: number|null; thesis: string; value_status: string|null; conviction: string|null; risk: string|null};
  verdicts?: Verdicts|null; change?: Change};
/** What changed since the latest frozen record of an earlier month. */
export type Change = {status: "no_record"|"new_holding"|"changed"|"unchanged"; since: string|null; previous_decision: Decision|null; summary: string; details: string[]};
export type Gone = {qualified_symbol: string; security_id: string|null; company_name: string|null; previous_decision: Decision; since: string};
export type Monthly = {decision_at: string; notice: string; synthetic_fixture: boolean; target_members: number; population: number;
  picks: Pick[]; verdict_rules?: Record<string, string>|null; holdings: Holding[]; no_longer_held?: Gone[]; totals: Total[]; counts: Record<Decision, number>;
  rules: Record<string, number>; method: string; label: string; allocation?: Allocation;
  plan?: {version: string; assumes: {cash_pool: number; prices_through: string|null}; valid_until: string}};
type Order = {action: string; qualified_symbol: string; security_id: string|null; company_name: string|null; shares: number; price: number; amount: number; why: string;
  weight_after?: number|null; amount_gbp?: number|null};
export type Account = {currency: string; cash_pool: number; overdrawn: boolean; uncounted_trades: number; deposited_this_month: number; monthly_contribution: number;
  contribution_included: number; available: number; gbp_per_usd: {rate: number; observed_on: string; source: "stored"|"your_last_trade"}|null;
  sale_proceeds: number|null; invested: number|null; left_as_cash: number; awaiting_proceeds?: number|null; reconciliation?: Reconciliation};
export type Allocation = {new_cash: number; reinvest_sales: boolean; sale_proceeds: number; available: number; sales: Order[]; buys: Order[];
  invested: number; left_as_cash: number; portfolio_after: number; rules: {position_limit: number; minimum_purchase_usd: number; top3_limit?: number|null}; method: string; label: string;
  max_holdings?: number|null; holdings_after?: number; skipped_no_slot?: {qualified_symbol: string; security_id: string|null; company_name: string|null}[]; account?: Account;
  held_back_buys?: Order[]; unfunded_picks?: {security_id: string; qualified_symbol: string; company_name: string|null; rank?: number|null}[]; top3_limited?: string[];
  awaiting_proceeds?: number};

export function AllocationView({allocation, decision, target, plan}: {allocation: Allocation; decision: string; target: number; plan?: Monthly["plan"]}) {
  const usd = (v: number) => money(v, "USD");
  const account = allocation.account;
  const pounds = (v: number|null|undefined) => v == null ? "—" : money(v, account?.currency ?? "GBP");
  const orders = [...allocation.sales, ...allocation.buys];
  const skipped = allocation.skipped_no_slot ?? [];
  return <section className="panel prototype-panel"><h2>Suggested allocation</h2>
    {account ? <>
      <p>{pounds(account.cash_pool)} in the cash pool{account.contribution_included ? ` + ${pounds(account.contribution_included)} planned contribution` : ""}
        {allocation.reinvest_sales && allocation.sale_proceeds > 0 ? ` + ${pounds(account.sale_proceeds)} from suggested sales` : ""}.
        {" "}Suggested buys use <b>{pounds(account.invested)}</b>; <b>{pounds(account.left_as_cash)}</b> stays as cash for later.</p>
      {!account.contribution_included && account.deposited_this_month === 0 && <p>This month&apos;s {pounds(account.monthly_contribution)} contribution isn&apos;t recorded yet. Include it above, or record it on the <Link href="/portfolio">Portfolio</Link> page once it arrives.</p>}
      {account.overdrawn && <p className="notice warning">Your cash pool is below zero, so a deposit is probably missing. It is treated as zero here.</p>}
      {account.uncounted_trades > 0 && <p className="notice warning">{account.uncounted_trades} trade(s) have no {account.currency} total, so the cash pool may not match your broker.</p>}
      {account.gbp_per_usd ? <p><small>Pounds converted at {account.gbp_per_usd.rate.toFixed(4)} per dollar ({account.gbp_per_usd.source === "stored" ? "stored rate" : "implied by your last dollar trade"}, {account.gbp_per_usd.observed_on}). Your broker&apos;s rate and fees will differ slightly.</small></p>
        : <p className="notice warning">No recent pound/dollar rate is stored and no dollar trade has a pound total, so no purchases can be sized yet.</p>}
    </> : <p>{usd(allocation.new_cash)} new money{allocation.reinvest_sales ? ` + ${usd(allocation.sale_proceeds)} from sales` : ""} = <b>{usd(allocation.available)}</b> to invest.
      {" "}Suggested buys use <b>{usd(allocation.invested)}</b>; <b>{usd(allocation.left_as_cash)}</b> stays as cash.</p>}
    {allocation.held_back_buys && allocation.held_back_buys.length > 0 && <p className="notice warning">
      {allocation.held_back_buys.length} suggested buy{allocation.held_back_buys.length === 1 ? " is" : "s are"} held back: {account?.reconciliation?.message} <Link href="/portfolio#reconcile">Check against your broker</Link>.</p>}
    {(account?.awaiting_proceeds ?? 0) > 0 && <p className="notice">About {pounds(account!.awaiting_proceeds)} more becomes available once you record the suggested sales.
      Buys are never funded by a sale that hasn&apos;t happened yet; the plan is recalculated after you record it.</p>}
    {orders.length === 0 ? <p>No orders suggested. Holding cash is a valid outcome when nothing qualifies or every position is at its limit.</p> :
      <div className="prototype-table-wrap"><table className="prototype-holdings-table"><thead><tr><th>Order</th><th>Stock</th><th>Shares</th><th>Approx. amount</th><th>Weight after</th><th>Why</th></tr></thead><tbody>
        {orders.map(o => <tr key={o.action + o.qualified_symbol}><td><span className={`prototype-decision prototype-decision-${o.action === "SELL" || o.action === "TRIM" ? (o.action === "SELL" ? "sell" : "reduce") : "buy-more"}`}>{o.action}</span></td>
          <td>{o.security_id ? <Link href={detailHref(o.security_id, decision, target)}>{o.qualified_symbol}</Link> : o.qualified_symbol}<small>{o.company_name}</small></td>
          <td>{o.shares.toLocaleString("en-US", {maximumFractionDigits: 4})}<small>at ~{usd(o.price)}</small></td><td>{account ? pounds(o.amount_gbp) : usd(o.amount)}{account && <small>{usd(o.amount)}</small>}</td>
          <td>{o.weight_after == null ? "—" : percent(o.weight_after)}</td><td>{o.why}</td></tr>)}
      </tbody></table></div>}
    {allocation.max_holdings != null && <p>Holdings after these orders: {allocation.holdings_after} of at most {allocation.max_holdings}.
      {skipped.length > 0 && <> No free place for {skipped.map(s => s.qualified_symbol).join(", ")}; a new name only replaces a holding when one is sold.</>}</p>}
    <p><small>{allocation.method} No position above {percent(allocation.rules.position_limit)}{allocation.rules.top3_limit ? `, the three largest within ${percent(allocation.rules.top3_limit)}` : ""}; purchases under {usd(allocation.rules.minimum_purchase_usd)} are skipped. Prices are the last stored close, so real fills will differ. {allocation.label}</small></p>
  {allocation.top3_limited && allocation.top3_limited.length > 0 && <p><small>Kept smaller so your three largest positions stay within the limit: {allocation.top3_limited.join(", ")}.</small></p>}
    {plan && <p className="plan-stamp"><small>Plan {plan.version}: assumes {pounds(plan.assumes.cash_pool)} cash and prices to {plan.assumes.prices_through ?? "—"}. Out of date when {plan.valid_until.charAt(0).toLowerCase() + plan.valid_until.slice(1)}</small></p>}
  </section>;
}

const signed = (value: number|null|undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${percent(value)}`;

/** One holding: the decision and what changed first, then why, then the plain
 *  answers, with the figures behind a disclosure. */
export function HoldingRow({h, link, rules}: {h: Holding; link: (h: Holding) => ReactNode; rules?: Record<string, string>|null}) {
  const answers = h.verdicts?.answers ?? [];
  const change = h.change;
  return <article className="holding-row">
    <div className="holding-name"><b>{link(h)}</b><small>{h.company_name}</small>
      <small>{h.market_value == null ? "no stored price" : `${money(h.market_value, h.currency)} · ${h.weight == null ? "—" : percent(h.weight)} of portfolio`}
        {h.unrealised_return != null && <> · <span className={h.unrealised_return >= 0 ? "gain" : "loss"}>{signed(h.unrealised_return)}</span></>}</small></div>
    <div className="holding-decision">
      <span className={`prototype-decision prototype-decision-${slug(h.decision)}`}>{h.decision}</span>
      {change && <p className={`holding-change holding-change-${change.status}`}>{change.summary}{change.details.length > 0 && <> {change.details.join(" ")}</>}</p>}
      <ul className="prototype-reasons">{h.reasons.map(r => <li key={r}>{r}</li>)}</ul>
      <p className="pick-meta">Thesis: {h.checks ? OVERALL[h.checks.overall] : OVERALL.not_covered}</p>
    </div>
    {answers.length > 0 && <div className="holding-answers">
      <ul className="verdict-tags">{answers.map(a => <li key={a.question} className={`verdict verdict-${a.level}`} title={a.headline}>
        <span className="verdict-tag">{QUESTION[a.question as VerdictQuestion]} · {LEVEL[a.level]}</span><span>{a.headline}</span></li>)}</ul>
      <details className="pick-details"><summary>Show figures and rules</summary>{answers.map(a => <Detail key={a.question} v={a} rule={rules?.[a.question]}/>)}</details>
    </div>}
  </article>;
}

const slug = (d: Decision) => d.toLowerCase().replace(" ", "-");
