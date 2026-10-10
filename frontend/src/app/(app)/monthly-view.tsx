import Link from "next/link";
import type {ReactNode} from "react";
import {OVERALL, type CompanyChecks} from "./checks-view";
import {money, type Total} from "./portfolio-view";
import {detailHref, percent} from "./view";
import {describeCutoff} from "./when";
import {Detail, LEVEL, PickCard, QUESTION, type Pick} from "./pick-card";
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
  rules: Record<string, number>; method: string; label: string; allocation?: Allocation};
type Order = {action: string; qualified_symbol: string; security_id: string|null; company_name: string|null; shares: number; price: number; amount: number; why: string;
  weight_after?: number|null; amount_gbp?: number|null};
export type Account = {currency: string; cash_pool: number; overdrawn: boolean; uncounted_trades: number; deposited_this_month: number; monthly_contribution: number;
  contribution_included: number; available: number; gbp_per_usd: {rate: number; observed_on: string; source: "stored"|"your_last_trade"}|null;
  sale_proceeds: number|null; invested: number|null; left_as_cash: number};
export type Allocation = {new_cash: number; reinvest_sales: boolean; sale_proceeds: number; available: number; sales: Order[]; buys: Order[];
  invested: number; left_as_cash: number; portfolio_after: number; rules: {position_limit: number; minimum_purchase_usd: number}; method: string; label: string;
  max_holdings?: number|null; holdings_after?: number; skipped_no_slot?: {qualified_symbol: string; security_id: string|null; company_name: string|null}[]; account?: Account};

export function AllocationView({allocation, decision, target}: {allocation: Allocation; decision: string; target: number}) {
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
    {orders.length === 0 ? <p>No orders suggested. Holding cash is a valid outcome when nothing qualifies or every position is at its limit.</p> :
      <div className="prototype-table-wrap"><table className="prototype-holdings-table"><thead><tr><th>Order</th><th>Stock</th><th>Shares</th><th>Approx. amount</th><th>Weight after</th><th>Why</th></tr></thead><tbody>
        {orders.map(o => <tr key={o.action + o.qualified_symbol}><td><span className={`prototype-decision prototype-decision-${o.action === "SELL" || o.action === "TRIM" ? (o.action === "SELL" ? "sell" : "reduce") : "buy-more"}`}>{o.action}</span></td>
          <td>{o.security_id ? <Link href={detailHref(o.security_id, decision, target)}>{o.qualified_symbol}</Link> : o.qualified_symbol}<small>{o.company_name}</small></td>
          <td>{o.shares.toLocaleString("en-US", {maximumFractionDigits: 4})}<small>at ~{usd(o.price)}</small></td><td>{account ? pounds(o.amount_gbp) : usd(o.amount)}{account && <small>{usd(o.amount)}</small>}</td>
          <td>{o.weight_after == null ? "—" : percent(o.weight_after)}</td><td>{o.why}</td></tr>)}
      </tbody></table></div>}
    {allocation.max_holdings != null && <p>Holdings after these orders: {allocation.holdings_after} of at most {allocation.max_holdings}.
      {skipped.length > 0 && <> No free place for {skipped.map(s => s.qualified_symbol).join(", ")}; a new name only replaces a holding when one is sold.</>}</p>}
    <p><small>{allocation.method} No position above {percent(allocation.rules.position_limit)}; purchases under {usd(allocation.rules.minimum_purchase_usd)} are skipped. Prices are the last stored close, so real fills will differ. {allocation.label}</small></p>
  </section>;
}

const signed = (value: number|null|undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${percent(value)}`;
const monthYear = (iso: string) => new Date(iso).toLocaleDateString("en-GB", {month: "long", year: "numeric", timeZone: "UTC"});

/** One holding: the decision and what changed first, then why, then the plain
 *  answers, with the figures behind a disclosure. */
function HoldingRow({h, link, rules}: {h: Holding; link: (h: Holding) => ReactNode; rules?: Record<string, string>|null}) {
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

const ORDER: Decision[] = ["SELL", "REDUCE", "REVIEW", "BUY MORE", "HOLD"];
const slug = (d: Decision) => d.toLowerCase().replace(" ", "-");

export function MonthlyView({monthly}: {monthly: Monthly}) {
  const link = (h: {security_id: string|null; qualified_symbol: string}) =>
    h.security_id ? <Link href={detailHref(h.security_id, monthly.decision_at, monthly.target_members)}>{h.qualified_symbol}</Link> : h.qualified_symbol;
  return <div className="brief-stack">
    {monthly.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence.</p>}
    <section className="panel prototype-panel"><p className="eyebrow">AS OF {describeCutoff(monthly.decision_at).toUpperCase()}</p><h2>What to do this month</h2>
      <p className="prototype-decision-counts">{ORDER.map(d => <span key={d} className={`prototype-decision prototype-decision-${slug(d)}`}>{d} {monthly.counts[d]}</span>)}</p>
      <p>{monthly.label}</p></section>

    <section className="panel prototype-panel"><h2>New opportunities</h2>
      <p>The month&apos;s undervaluation ranking, from {monthly.population} eligible companies. Places are never filled with weaker names.</p>
      {monthly.picks.length === 0 ? <p>No company qualifies this month. Keeping cash or your current holdings is a valid outcome.</p> :
        <div className="pick-cards">{monthly.picks.map(p => <PickCard key={p.security_id} p={p} decision={monthly.decision_at} target={monthly.target_members} rules={monthly.verdict_rules}/>)}</div>}</section>

    <section className="panel prototype-panel"><h2>Your holdings</h2>
      {monthly.holdings.length === 0 ? <p>No holdings recorded before this cutoff. Record your trades on the <Link href="/portfolio">Portfolio</Link> page.</p> :
        <div className="holding-rows">{monthly.holdings.map(h => <HoldingRow key={h.qualified_symbol + h.currency} h={h} link={link} rules={monthly.verdict_rules}/>)}</div>}
      {(monthly.no_longer_held ?? []).length > 0 && <p className="pick-meta">No longer held since {monthYear(monthly.no_longer_held![0].since)}: {monthly.no_longer_held!.map(g => `${g.qualified_symbol} (was ${g.previous_decision})`).join(", ")}.</p>}
      <p><small>{monthly.method} BUY MORE needs at least {percent(monthly.rules.buy_more_minimum_upside)} upside and a position under {percent(monthly.rules.maximum_position_weight_for_buying)};
        REDUCE when the price is above the middle-case value or a position exceeds {percent(monthly.rules.reduce_above_position_weight)}; SELL when the thesis breaks or the price is {percent(-monthly.rules.sell_below_upside)} above the middle-case value.
        In between, HOLD, so small monthly moves do not cause trades.</small></p></section>
    {monthly.allocation && <AllocationView allocation={monthly.allocation} decision={monthly.decision_at} target={monthly.target_members}/>}
  </div>;
}
