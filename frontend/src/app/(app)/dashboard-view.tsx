import Link from "next/link";
import type {ReactNode} from "react";
import {Icon} from "./app-shell";
import {AllocationView, HoldingRow, type Holding, type Monthly} from "./monthly-view";
import {Detail, LEVEL, QUESTION, type Pick} from "./pick-card";
import {money} from "./portfolio-view";
import {detailHref, percent} from "./view";
import type {Verdict, VerdictQuestion} from "./value-view";

/** What the dashboard reads from the portfolio endpoint: the cash ledger's entries, newest first. */
export type Activity = {cash: {entries: {on: string; kind: "deposit"|"withdrawal"|"buy"|"sell"; amount: number; qualified_symbol?: string}[]}};

const gbp = (v: number|null|undefined) => v == null ? "—" : money(v, "GBP");
const signedPct = (v: number|null|undefined) => v == null ? "—" : `${v > 0 ? "+" : ""}${percent(v)}`;
const PALETTE = ["#3b82f6", "#22c55e", "#a855f7", "#f59e0b", "#14b8a6", "#ef4444", "#94a3b8"];

/** Portfolio figures in pounds: holdings at the latest stored close (dollars at the
 *  stored or last-trade rate) plus the cash pool. Null where a rate is missing. */
export function figures(m: Monthly) {
  const account = m.allocation?.account;
  const rate = account?.gbp_per_usd?.rate ?? null;
  const toGbp = (value: number|null, currency: string) => value == null ? null : currency === "GBP" ? value : currency === "USD" && rate ? value * rate : null;
  const priced = m.totals.filter(t => t.market_value != null);
  // A currency without a rate leaves the pound totals unknown rather than partial.
  const known = priced.every(t => toGbp(t.market_value, t.currency) != null);
  const add = (pick: (t: typeof priced[number]) => number|null) => known ? priced.reduce((s, t) => s + (toGbp(pick(t), t.currency) ?? 0), 0) : null;
  const holdings = add(t => t.market_value), cost = add(t => t.priced_cost_basis), profit = add(t => t.unrealised_profit);
  const cash = account ? Math.max(0, account.cash_pool) : null;
  const value = holdings != null && cash != null ? holdings + cash : null;
  const segments = m.holdings.filter(h => h.market_value != null).map(h => ({label: h.qualified_symbol.replace(/\.US$/, ""), value: toGbp(h.market_value, h.currency)}))
    .filter((s): s is {label: string; value: number} => s.value != null && s.value > 0).sort((a, b) => b.value - a.value);
  const shown = segments.slice(0, 5);
  const other = segments.slice(5).reduce((sum, s) => sum + s.value, 0);
  if (other > 0) shown.push({label: "Other holdings", value: other});
  if (cash) shown.push({label: "Cash", value: cash});
  return {account, rate, holdings, cost, profit, cash, value, segments: shown, profitReturn: profit != null && cost ? profit / cost : null};
}

function Stat({label, icon, children}: {label: string; icon?: ReactNode; children: ReactNode}) {
  return <div className="stat-card"><div className="stat-label">{label}{icon}</div>{children}</div>;
}

function Donut({segments, total}: {segments: {label: string; value: number}[]; total: number|null}) {
  const sum = segments.reduce((s, x) => s + x.value, 0);
  const r = 15.915; // circumference 100
  let offset = 25;
  return <div className="donut-wrap">
    <svg viewBox="0 0 42 42" className="donut" role="img" aria-label="Portfolio allocation">
      <circle cx="21" cy="21" r={r} fill="none" stroke="var(--line)" strokeWidth="6"/>
      {sum > 0 && segments.map((s, i) => {
        const share = s.value / sum * 100;
        const arc = <circle key={s.label} cx="21" cy="21" r={r} fill="none" stroke={PALETTE[i % PALETTE.length]} strokeWidth="6"
          strokeDasharray={`${share} ${100 - share}`} strokeDashoffset={offset}/>;
        offset -= share;
        return arc;
      })}
      <text x="21" y="21" textAnchor="middle" className="donut-total">{gbp(total)}</text>
      <text x="21" y="26" textAnchor="middle" className="donut-caption">Total value</text>
    </svg>
    <ul className="donut-legend">{segments.map((s, i) => <li key={s.label}><span style={{background: PALETTE[i % PALETTE.length]}}/>{s.label}<b>{sum ? `${Math.round(s.value / sum * 100)}%` : "—"}</b></li>)}</ul>
  </div>;
}

const TAG: Record<VerdictQuestion, Partial<Record<string, string>>> = {
  cheap: {positive: "Undervalued", mixed: "Slightly cheap"}, quality: {positive: "Quality business", negative: "Weak business"},
  growth: {positive: "Growing", negative: "Shrinking"}, risk: {positive: "No warning signs", negative: "Several risks"},
};
function tags(answers: Verdict[]) {
  return answers.map(a => ({text: TAG[a.question as VerdictQuestion]?.[a.level], level: a.level})).filter((t): t is {text: string; level: Verdict["level"]} => !!t.text).slice(0, 3);
}
function reason(answers: Verdict[]) {
  const cheap = answers.find(a => a.question === "cheap"), quality = answers.find(a => a.question === "quality");
  return [cheap?.headline.split(":")[0], quality?.headline].filter(Boolean).join(". ").replace(/\.\.$/, ".");
}

function PickRow({p, m}: {p: Pick; m: Monthly}) {
  const answers = p.verdicts?.answers ?? [];
  const buy = m.allocation?.buys.find(o => o.security_id === p.security_id);
  const action = buy ? {kind: "buy", word: "Buy", amount: buy.amount_gbp != null ? gbp(buy.amount_gbp) : money(buy.amount, "USD"), note: "Suggested allocation"}
    : p.held ? {kind: "held", word: "Held", amount: "", note: "Already in your portfolio"}
    : {kind: "watch", word: "Watch", amount: "", note: "No cash allocated this month"};
  const points = p.conviction_points ?? 0;
  return <li className="pick-row">
    <span className={`pick-rank rank-${p.rank}`}>#{p.rank}</span>
    <div className="pick-name"><Link href={detailHref(p.security_id, m.decision_at, m.target_members)}><b>{p.qualified_symbol.replace(/\.US$/, "")}</b></Link><small>{p.company_name}</small></div>
    <div className="pick-why">
      <div className="pick-tags">{tags(answers).map(t => <span key={t.text} className={`chip chip-${t.level}`}>{t.text}</span>)}</div>
      <p>{answers.length ? reason(answers) : `Estimated upside ${signedPct(p.upside)}.`}</p>
      {answers.length > 0 && <details className="pick-details"><summary>Why, with figures</summary>
        <ul className="verdict-tags">{answers.map(a => <li key={a.question} className={`verdict verdict-${a.level}`}><span className="verdict-tag">{QUESTION[a.question as VerdictQuestion]} · {LEVEL[a.level]}</span><span>{a.headline}</span></li>)}</ul>
        {answers.map(a => <Detail key={a.question} v={a} rule={m.verdict_rules?.[a.question]}/>)}
        {p.next_results_estimate && <p className="pick-meta">{p.next_results_estimate < m.decision_at.slice(0, 10)
          ? `Results were expected around ${p.next_results_estimate}; none are filed yet at this cutoff.` : `Next results ≈ ${p.next_results_estimate}`}</p>}</details>}
    </div>
    <div className="pick-conviction"><small>Conviction</small>
      <span className="bar" aria-label={`Conviction ${points} of 5`}>{[1, 2, 3, 4, 5].map(i => <i key={i} className={i <= points ? "on" : ""}/>)}</span>
      <b>{p.conviction ? p.conviction[0].toUpperCase() + p.conviction.slice(1) : "—"}</b></div>
    <Link href={detailHref(p.security_id, m.decision_at, m.target_members)} className={`pick-action action-${action.kind}`}>
      <small>Action</small><b>{action.word}</b>{action.amount && <span>{action.amount}</span>}<small>{action.note}</small><span className="chev" aria-hidden="true">›</span></Link>
  </li>;
}

const SLUG = (d: string) => d.toLowerCase().replace(" ", "-");

function PortfolioTable({m}: {m: Monthly}) {
  if (m.holdings.length === 0) return <p className="muted">No holdings recorded before this cutoff. <Link href="/portfolio#record-trade">Record a trade</Link>.</p>;
  return <div className="table-wrap"><table className="dash-table"><thead><tr><th>Ticker</th><th>Shares</th><th>Avg. price</th><th>Current price</th><th>Value</th><th>Gain/Loss</th><th>Action</th></tr></thead><tbody>
    {m.holdings.map(h => <tr key={h.qualified_symbol + h.currency}>
      <td><span className="ticker"><span className="avatar">{h.qualified_symbol[0]}</span>{h.security_id ? <Link href={detailHref(h.security_id, m.decision_at, m.target_members)}>{h.qualified_symbol.replace(/\.US$/, "")}</Link> : h.qualified_symbol}</span></td>
      <td>{h.shares.toLocaleString("en-US", {maximumFractionDigits: 4})}</td>
      <td>{h.average_cost == null ? "—" : money(h.average_cost, h.currency)}</td>
      <td>{h.price ? money(h.price.close, h.currency) : "—"}</td>
      <td>{h.market_value == null ? "—" : money(h.market_value, h.currency)}</td>
      <td className={h.unrealised_return == null ? "" : h.unrealised_return >= 0 ? "gain" : "loss"}>{signedPct(h.unrealised_return)}</td>
      <td><span className={`chip decision-${SLUG(h.decision)}`}>{h.decision}</span>{h.change && h.change.status !== "no_record" && <small className="table-note">{h.change.summary}</small>}</td></tr>)}
  </tbody></table></div>;
}

function RecentActivity({m, activity}: {m: Monthly; activity: Activity|null}) {
  const today = m.decision_at.slice(0, 10);
  const items: {on: string; icon: string; title: string; note: string}[] = [
    {on: today, icon: "↗", title: "Monthly analysis", note: `${m.picks.length} pick${m.picks.length === 1 ? "" : "s"} from ${m.population} companies`},
    ...m.picks.filter(p => p.next_results_estimate && p.next_results_estimate >= today)
      .map(p => ({on: p.next_results_estimate!, icon: "◷", title: `Results expected: ${p.qualified_symbol.replace(/\.US$/, "")}`, note: "Estimated from past filing dates"})),
    ...(activity?.cash.entries ?? []).slice(0, 6).map(e => ({on: e.on, icon: e.kind === "deposit" ? "+" : e.kind === "withdrawal" ? "−" : "⇄",
      title: e.kind === "deposit" ? `${gbp(Math.abs(e.amount))} deposit recorded` : e.kind === "withdrawal" ? `${gbp(Math.abs(e.amount))} withdrawal recorded`
        : `${e.kind === "buy" ? "Bought" : "Sold"} ${e.qualified_symbol ?? ""}`, note: e.kind === "deposit" || e.kind === "withdrawal" ? "Cash pool" : gbp(Math.abs(e.amount))})),
  ].sort((a, b) => b.on.localeCompare(a.on)).slice(0, 6);
  return <ul className="activity">{items.map(i => <li key={i.title + i.on}><span className="activity-icon" aria-hidden="true">{i.icon}</span>
    <div><b>{i.title}</b><small>{i.note}</small></div><time>{i.on}</time></li>)}</ul>;
}

export function greeting(hour: number) {return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";}

/** The home page, laid out as an investment overview: the numbers that matter,
 *  this month's picks, holdings with their decisions, then the details. */
export function DashboardView({m, activity, toolbar, keepScore}: {m: Monthly; activity: Activity|null; toolbar?: ReactNode; keepScore?: ReactNode}) {
  const f = figures(m);
  const budget = f.account?.monthly_contribution ?? null, deposited = f.account?.deposited_this_month ?? 0;
  const holdings: Holding[] = m.holdings;
  return <div className="dashboard">
    {m.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence.</p>}
    {toolbar}
    {f.account?.reconciliation && f.account.reconciliation.status !== "matched" && <p className={`notice${f.account.reconciliation.blocks_buys ? " warning" : ""}`}>
      {f.account.reconciliation.message} <Link href="/portfolio#reconcile">{f.account.reconciliation.blocks_buys ? "Fix it" : "Check now"}</Link></p>}
    <section className="stat-grid">
      <Stat label="Portfolio value"><b className="stat-value">{gbp(f.value)}</b>
        {f.profit != null && <span className={f.profit >= 0 ? "gain" : "loss"}>{f.profit >= 0 ? "+" : "−"}{gbp(Math.abs(f.profit))} ({signedPct(f.profitReturn)})</span>}
        <small>Holdings + cash</small></Stat>
      <Stat label="Invested" icon={<Icon name="portfolio"/>}><b className="stat-value">{gbp(f.cost)}</b><small>{holdings.length} {holdings.length === 1 ? "stock" : "stocks"}</small></Stat>
      <Stat label="Cash available" icon={<Icon name="portfolio"/>}><b className="stat-value">{gbp(f.cash)}</b><small>{f.account?.overdrawn ? "Below zero: a deposit may be missing" : "In your cash pool"}</small></Stat>
      <Stat label="This month's deposit" icon={<Icon name="picks"/>}><b className="stat-value">{gbp(deposited)}</b>
        {budget ? <><span className="progress"><i style={{width: `${Math.min(100, deposited / budget * 100)}%`}}/></span>
          <small>{deposited >= budget ? `Planned ${gbp(budget)} arrived` : `of ${gbp(budget)} planned; unspent cash carries over`}</small></> : <small>Set your plan on the Portfolio page</small>}</Stat>
    </section>
    <div className="dash-grid">
      <section className="card picks-card"><header><h2>This month&apos;s top picks</h2><Link href="/shortlist">View all picks →</Link></header>
        {m.picks.length === 0 ? <p className="muted">No company qualifies this month. Keeping cash or your current holdings is a valid outcome.</p>
          : <ol className="pick-rows">{m.picks.map(p => <PickRow key={p.security_id} p={p} m={m}/>)}</ol>}</section>
      <div className="side-stack">
        <section className="card"><header><h2>Portfolio allocation</h2></header>
          {f.segments.length ? <Donut segments={f.segments} total={f.value}/> : <p className="muted">Nothing to show until a holding or cash is recorded.</p>}</section>
        <section className="card"><header><h2>Quick actions</h2></header>
          <div className="quick-actions">
            <Link href="/portfolio#record-trade"><b>Add transaction</b><small>Buy, sell or deposit</small></Link>
            <Link href="/shortlist"><b>Search company</b><small>Every eligible company</small></Link>
            <Link href="/watchlist"><b>View watchlist</b><small>Keep an eye on stocks</small></Link>
            <Link href="/scorecard"><b>History</b><small>You vs VALL, past calls</small></Link>
          </div></section>
      </div>
      <section className="card portfolio-card"><header><h2>Your portfolio</h2><Link href="/portfolio">View full portfolio →</Link></header><PortfolioTable m={m}/>
        {(m.no_longer_held ?? []).length > 0 && <p className="muted">No longer held: {m.no_longer_held!.map(g => `${g.qualified_symbol} (was ${g.previous_decision})`).join(", ")}.</p>}</section>
      <section className="card activity-card"><header><h2>Recent activity</h2><Link href="/portfolio">View all →</Link></header><RecentActivity m={m} activity={activity}/></section>
    </div>
    {holdings.length > 0 && <section className="card"><header><h2>Why each decision</h2></header>
      <div className="holding-rows">{holdings.map(h => <HoldingRow key={h.qualified_symbol + h.currency} h={h} rules={m.verdict_rules}
        link={x => x.security_id ? <Link href={detailHref(x.security_id, m.decision_at, m.target_members)}>{x.qualified_symbol}</Link> : x.qualified_symbol}/>)}</div>
      <p className="muted"><small>{m.method} BUY MORE needs at least {percent(m.rules.buy_more_minimum_upside)} upside and a position under {percent(m.rules.maximum_position_weight_for_buying)};
        REDUCE when the price is above the middle-case value; a position above {percent(m.rules.reduce_above_position_weight)} is flagged for review, never sold just for its size; SELL when the thesis breaks or the price is {percent(-m.rules.sell_below_upside)} above the middle-case value.
        In between, HOLD, so small monthly moves do not cause trades.</small></p></section>}
    {m.allocation && <AllocationView allocation={m.allocation} decision={m.decision_at} target={m.target_members} plan={m.plan}/>}
    {keepScore}
    <p className="muted"><small>{m.label}</small></p>
  </div>;
}
