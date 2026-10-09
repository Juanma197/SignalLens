import Link from "next/link";
import {OVERALL, type CompanyChecks} from "./checks-view";
import {money, type Total} from "./portfolio-view";
import {detailHref, percent} from "./view";
import type {ValueAssessment} from "./value-view";

export type Decision = "SELL"|"REDUCE"|"REVIEW"|"BUY MORE"|"HOLD";
export type Holding = {qualified_symbol: string; security_id: string|null; company_name: string|null; currency: string; shares: number;
  average_cost: number|null; cost_basis: number; price: {close: number; trading_date: string}|null; market_value: number|null;
  unrealised_return: number|null; weight: number|null; checks: CompanyChecks|null; decision: Decision; reasons: string[];
  evidence: {upside: number|null; weight: number|null; thesis: string; value_status: string|null; conviction: string|null; risk: string|null}};
export type Monthly = {decision_at: string; notice: string; synthetic_fixture: boolean; target_members: number; population: number;
  picks: (ValueAssessment & {held: boolean})[]; holdings: Holding[]; totals: Total[]; counts: Record<Decision, number>;
  rules: Record<string, number>; method: string; label: string};

const RECOMMENDATION = {strong_buy: "Strong Buy", buy: "Buy"};
const signed = (value: number|null|undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${percent(value)}`;
const ORDER: Decision[] = ["SELL", "REDUCE", "REVIEW", "BUY MORE", "HOLD"];
const slug = (d: Decision) => d.toLowerCase().replace(" ", "-");

export function MonthlyView({monthly}: {monthly: Monthly}) {
  const link = (h: {security_id: string|null; qualified_symbol: string}) =>
    h.security_id ? <Link href={detailHref(h.security_id, monthly.decision_at, monthly.target_members)}>{h.qualified_symbol}</Link> : h.qualified_symbol;
  return <div className="brief-stack">
    {monthly.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence.</p>}
    <section className="panel prototype-panel"><p className="eyebrow">DECISIONS AT {monthly.decision_at}</p><h2>What to do this month</h2>
      <p className="prototype-decision-counts">{ORDER.map(d => <span key={d} className={`prototype-decision prototype-decision-${slug(d)}`}>{d} {monthly.counts[d]}</span>)}</p>
      <p>{monthly.label}</p></section>

    <section className="panel prototype-panel"><h2>New opportunities</h2>
      <p>The month&apos;s undervaluation ranking, from {monthly.population} eligible companies. Places are never filled with weaker names.</p>
      {monthly.picks.length === 0 ? <p>No company qualifies this month. Keeping cash or your current holdings is a valid outcome.</p> :
        <div className="prototype-table-wrap"><table className="prototype-value-table"><thead><tr><th>Rank</th><th>Stock</th><th>Estimated upside</th><th>Recommendation</th><th>Conviction</th><th>Risk</th></tr></thead><tbody>
          {monthly.picks.map(p => <tr key={p.security_id}><td>#{p.rank}</td><td>{link(p)}<small>{p.company_name}{p.held ? " · already held" : ""}</small></td>
            <td>{signed(p.upside)}</td><td>{RECOMMENDATION[p.recommendation!]}</td><td>{p.conviction}</td><td>{p.risk}</td></tr>)}
        </tbody></table></div>}</section>

    <section className="panel prototype-panel"><h2>Your holdings</h2>
      {monthly.holdings.length === 0 ? <p>No holdings recorded before this cutoff. Record your trades on the <Link href="/prototype/portfolio">Portfolio</Link> page.</p> :
        <div className="prototype-table-wrap"><table className="prototype-holdings-table"><thead><tr><th>Holding</th><th>Decision</th><th>Why</th><th>Upside</th><th>Weight</th><th>Thesis</th></tr></thead><tbody>
          {monthly.holdings.map(h => <tr key={h.qualified_symbol + h.currency}>
            <td><b>{link(h)}</b><small>{h.company_name}</small></td>
            <td><span className={`prototype-decision prototype-decision-${slug(h.decision)}`}>{h.decision}</span></td>
            <td><ul className="prototype-reasons">{h.reasons.map(r => <li key={r}>{r}</li>)}</ul></td>
            <td>{signed(h.evidence.upside)}</td>
            <td>{h.weight == null ? "—" : percent(h.weight)}<small>{h.market_value == null ? "no stored price" : money(h.market_value, h.currency)}</small></td>
            <td>{h.checks ? OVERALL[h.checks.overall] : OVERALL.not_covered}</td></tr>)}
        </tbody></table></div>}
      <p><small>{monthly.method} BUY MORE needs at least {percent(monthly.rules.buy_more_minimum_upside)} upside and a position under {percent(monthly.rules.maximum_position_weight_for_buying)};
        REDUCE when the price is above the middle-case value or a position exceeds {percent(monthly.rules.reduce_above_position_weight)}; SELL when the thesis breaks or the price is {percent(-monthly.rules.sell_below_upside)} above the middle-case value.
        In between, HOLD, so small monthly moves do not cause trades.</small></p></section>
  </div>;
}
