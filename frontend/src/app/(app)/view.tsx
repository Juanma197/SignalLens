import Link from "next/link";
import {useState} from "react";
import {describeCutoff} from "./when";
import type {ValueRanking} from "./value-view";

export type Calculation = {formula: string; start_session: string; end_session: string; start_adjusted_close: number; end_adjusted_close: number; session_intervals: number; momentum_return: number; source: string[]; latest_input_retrieved_at: string};
export type Fact = {field: string; value: number; unit: string; concept: string; reported_start: string|null; reported_end: string; period_kind: string; reported_days?: number|null; form: string; public_at: string; retrieved_at: string; known_at: string; citation: {fact_key: string; accession: string; cik: string; source_endpoint: string}};
export type Company = {security_id: string; direct_field_count?: number; qualified_symbol: string|null; company_name: string|null; eligible: boolean; reasons: string[]; calculation: Calculation|null; direct_evidence: Fact[]; missing_data: {field: string; reasons: string[]}[]; risks: string[]; identity_evidence: Record<string, string|null>|null; action_coverage: ActionCoverage|null; industry?: Industry|null; size?: Size|null; financials?: Financials|null; valuation?: Valuation|null; sector_notes?: string[]; events?: Events|null; brief?: AnalystBrief|null};
export type FinValue = {value: number; concept: string; accession: string; form: string; known_at: string};
export type FinYear = {fiscal_year_end: string; values: Record<string, FinValue>; calculated: Record<string, number>};
export type Observation = {kind: "strength"|"weakness"|"neutral"|"gap"; area: string; text: string; fiscal_years: string[]};
export type Financials = {years: FinYear[]; observations: Observation[]; method: string; not_available: string[]; tables_omitted?: string; fiscal_years_available?: number};
export type Valuation = {market_cap_usd: number; fiscal_year_end: string; multiples: Partial<Record<"price_to_earnings"|"price_to_sales"|"price_to_free_cash_flow"|"price_to_book", number>>; not_meaningful: string[]; basis: string; earnings_yield?: number; free_cash_flow_yield?: number; history?: ValuationHistory|null; scenarios?: Scenarios|null};
export type Scenarios = {available: boolean; reason?: string; measure?: string; multiple_name?: string; years_used?: number; multiples_used?: number; price: number; price_session?: string; book_value_per_share: number|null; label?: string; volatility_note?: string|null;
  multiple_sensitivity?: {low_multiple: number; high_multiple: number; low_value_per_share: number; high_value_per_share: number}|null;
  cases?: {case: string; assumption: string; profit: number; multiple: number; value_per_share: number|null; vs_price: number|null; note?: string}[]};
export type AnalystBrief = {label: string; sections: {title: string; points: string[]}[]; counterarguments: string[]; missing_evidence: string[]};
export type FilingEvent = {accession: string; form: string; filing_date: string; known_at: string; items: string[]; category: string; label: string; confidence: number; amendment: boolean; url: string|null};
export type Events = {window_days: number; event_count: number; counts: Record<string, number>; note: string; latest_known_at: string|null;
  flags: {kind: "risk"|"catalyst"; category: string; count: number; text: string}[];
  results_timing: {last_results_filed: string; typical_gap_days: number; next_results_estimate: string; text: string}|null; events: FilingEvent[]};
type MultipleKey = "price_to_earnings"|"price_to_sales"|"price_to_free_cash_flow"|"price_to_book";
export type ValuationHistory = {basis: string; unavailable: string|null;
  years: {fiscal_year_end: string; price_session: string; close: number; diluted_shares: number; multiples: Partial<Record<MultipleKey, number>>}[];
  current: {basis_fiscal_year_end: string; close: number; multiples: Partial<Record<MultipleKey, number>>}|null;
  comparisons: {multiple: MultipleKey; position: "below"|"within"|"above"; text: string}[]};
export type ActionCoverage = Record<string, string|null> & {extension?: {stored_record_through: string; dividend_refresh_completed_at: string; source: string}|null};
export type Industry = {sic: number; sic_description: string|null; entity_type: string|null; sec_name: string|null; retrieved_at: string; response_sha256: string};
export type Size = {market_cap_usd: number; shares_outstanding: number; share_classes_summed: number; shares_as_of: string; shares_accession: string; shares_filed: string; shares_form: string; close: number; close_session: string; band_usd: [number, number]};
export type Report = {value_ranking?: ValueRanking; notice: string; version: string; configuration_hash: string; configuration: {screen: string; membership_order: string; result_order: string; maximum_direct_fact_age_days: number}; decision_at: string; validation_credit: number; membership_state: string; eligible_count: number; minimum_members: number; target_members: number; eligible_roster: string[]; proposed_membership: string[]; results: string[]; companies: Company[]; blockers: string[]; withholding_counts: Record<string, number>; source_schema_states: Record<string,string>; session_calendar: {derived_sessions: number; candidate_dates: number; us_symbols_priced: number; minimum_symbols_per_session: number; last_session: string|null}|null; synthetic_fixture: boolean};
export type Detail = Pick<Report,"notice"|"version"|"decision_at"|"blockers"|"membership_state"|"synthetic_fixture"> & {company: Company; proposed_member: boolean; qualifying_result: boolean};

export const percent = (value: number) => `${(value*100).toFixed(2)}%`;
export const money = (value: number) => value >= 1e9 ? `$${(value/1e9).toFixed(2)}B` : `$${(value/1e6).toFixed(0)}M`;
const healthLine = (f: Financials) => {
  const n = (kind: string) => f.observations.filter(o => o.kind === kind).length;
  return `Financial health: ${n("strength")} strengths · ${n("weakness")} weaknesses · ${n("gap")} gaps`;
};
const eventsLine = (e: Events) => [e.results_timing && `Next results ≈ ${e.results_timing.next_results_estimate}`,
  e.flags.filter(f => f.kind === "risk").length > 0 && `${e.flags.filter(f => f.kind === "risk").length} filing risk flag(s)`].filter(Boolean).join(" · ") || `${e.event_count} filing events in the last year`;
const valuationLine = (v: Valuation) => {
  const below = v.history?.comparisons.filter(c => c.position === "below").length ?? 0;
  const above = v.history?.comparisons.filter(c => c.position === "above").length ?? 0;
  return [v.multiples.price_to_earnings && `P/E ${v.multiples.price_to_earnings.toFixed(1)}`,
    v.free_cash_flow_yield !== undefined && `FCF yield ${(v.free_cash_flow_yield * 100).toFixed(1)}%`,
    v.history && v.history.comparisons.length > 0 && `vs own history: ${below} below, ${above} above range`].filter(Boolean).join(" · ") || "Valuation multiples not meaningful";
};
const sizeLine = (c: Company) => [c.size ? `${money(c.size.market_cap_usd)} market cap` : null, c.industry?.sic_description ?? null].filter(Boolean).join(" · ");
export const detailHref = (id: string, decision: string, target=15) => `/company/${encodeURIComponent(id)}?decision_at=${encodeURIComponent(decision)}&target_members=${target}`;
const words = (value: string) => value.replaceAll("_", " ");

/** The first rows of a long list, with a button for the rest: with the full US
 *  catalogue a table can have hundreds of rows. */
export function useShown<T>(rows: T[], first = 25) {
  const [all, setAll] = useState(false);
  const more = !all && rows.length > first
    ? <p><button type="button" className="prototype-more" onClick={() => setAll(true)}>Show all {rows.length}</button></p> : null;
  return [all ? rows : rows.slice(0, first), more] as const;
}

export function PrototypeNotice() {
  return <p className="notice warning">UNVALIDATED RESEARCH PROTOTYPE — ZERO VALIDATION CREDIT. Results describe past price behaviour. Membership is proposed and unfrozen; operator roster review is pending.</p>;
}

export function CalculationView({calculation}: {calculation: Calculation|null}) {
  if (!calculation) return <p>126-session calculation withheld: required price evidence is unavailable or invalid.</p>;
  return <div className="prototype-calculation"><p><b>126-session adjusted-close return: {percent(calculation.momentum_return)}</b></p>
    <p><code>{calculation.end_adjusted_close.toPrecision(8)} / {calculation.start_adjusted_close.toPrecision(8)} − 1</code></p>
    <dl><dt>Start session</dt><dd>{calculation.start_session}</dd><dt>End session</dt><dd>{calculation.end_session}</dd><dt>Intervals</dt><dd>{calculation.session_intervals} completed sessions</dd><dt>Price source</dt><dd>{calculation.source.join(", ")}</dd><dt>Latest input retrieval</dt><dd>{calculation.latest_input_retrieved_at}</dd></dl>
    <p>Fixed rule: positive return. Results use return descending, then durable ID ascending. No scoring weights; financial evidence has no ranking effect.</p></div>;
}

export function RosterView({report}: {report: Report}) {
  const companies = new Map(report.companies.map(c=>[c.security_id,c]));
  const [roster, moreRoster] = useShown(report.eligible_roster);
  const withheld = report.companies.filter(c=>!c.eligible);
  return <div className="brief-stack">
    {report.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence. This is not the actual operator roster.</p>}
    <section className="panel prototype-panel"><h2>Proposed research shortlist</h2><p>As of {describeCutoff(report.decision_at)}. {report.proposed_membership.length} proposed members from {report.eligible_count} eligible companies; minimum {report.minimum_members}, target {report.target_members}.</p>
      {report.blockers.length > 0 && <p role="status" className="notice warning">Results withheld: {report.blockers.map(words).join(" · ")}. Requirements remain unchanged.</p>}
      {report.results.length === 0 ? <p>No qualifying results at this cutoff.</p> : <div className="prototype-cards">{report.results.map((id,index)=>{const c=companies.get(id)!;return <article key={id}><p className="eyebrow">Research result {index+1}</p><h3>{c.qualified_symbol}</h3><p>{c.company_name}</p><p>{sizeLine(c)}</p>{c.financials && <p>{healthLine(c.financials)}</p>}{c.valuation && <p>{valuationLine(c.valuation)}</p>}{c.events && <p>{eventsLine(c.events)}</p>}<b>{percent(c.calculation!.momentum_return)} over 126 sessions</b><p>Qualified with positive momentum, identity checked at the cutoff, complete price/action coverage and direct financial context.</p><p>{c.risks.join(" ")}</p><Link href={detailHref(id,report.decision_at,report.target_members)}>View calculation and evidence</Link></article>;})}</div>}
    </section>
    {report.eligible_roster.length > 0 && <CompareView report={report} companies={companies}/>}
    <section className="panel prototype-panel"><h2>Actual eligible roster for review</h2><p>All eligible identities are listed below. Proposed membership is the first {report.target_members} in the fixed hash order. No universe has been frozen.</p><p><code>{report.configuration.membership_order}</code></p>
      <div className="prototype-table-wrap"><table><thead><tr><th>Order</th><th>Company / durable ID</th><th>Membership proposal</th><th>Evidence</th></tr></thead><tbody>{roster.map((id,index)=>{const c=companies.get(id)!;return <tr key={id}><td>{index+1}</td><td><Link href={detailHref(id,report.decision_at,report.target_members)}>{c.qualified_symbol} · {c.company_name}</Link><small>{sizeLine(c)}</small><small>{id}</small></td><td>{report.proposed_membership.includes(id)?"Proposed · unfrozen":"Outside bounded proposal"}</td><td>{c.direct_field_count ?? new Set(c.direct_evidence.map(f=>f.field)).size} direct fields; {c.missing_data.length} missing</td></tr>;})}</tbody></table></div>{moreRoster}
      {report.eligible_roster.length===0 && <p>No eligible identities can be established.</p>}
    </section>
    <section className="panel prototype-panel"><h2>Withheld companies and exact blockers</h2><p>Counts may overlap: a company can have several blockers.</p><ul>{Object.entries(report.withholding_counts).map(([reason,count])=><li key={reason}><code>{reason}</code>: {count}</li>)}</ul>
      {withheld.length > 0 && <details><summary>Every withheld company ({withheld.length})</summary>{withheld.map(c=><p key={c.security_id}><Link href={detailHref(c.security_id,report.decision_at,report.target_members)}>{c.qualified_symbol??c.security_id}</Link> — {c.reasons.join("; ")}</p>)}</details>}
      <details><summary>Stored schema availability</summary><ul>{Object.entries(report.source_schema_states).map(([table,state])=><li key={table}>{table}: {state}</li>)}</ul></details>
    </section>
    <section className="panel prototype-panel"><h2>Method and scope</h2><p>Version {report.version}. Configuration <code>{report.configuration_hash}</code>.</p><p>Direct facts retain their reported periods and source references. A usable direct observation is required; missing financial fields remain unknown. Maximum direct-fact age: {report.configuration.maximum_direct_fact_age_days} days. No accounting constructions are added.</p>{report.session_calendar&&<p>Session calendar derived from stored prices: {report.session_calendar.derived_sessions} sessions (latest {report.session_calendar.last_session}) from {report.session_calendar.candidate_dates} priced dates; a date counts when at least {report.session_calendar.minimum_symbols_per_session} of {report.session_calendar.us_symbols_priced} US symbols have a price.</p>}<p>Track A and Track B remain separate. Watchlist persistence, monthly snapshots and subsequent-performance tracking are pending the next slice in a separate prototype database.</p></section>
  </div>;
}

export function CompanyView({detail}: {detail: Detail}) {
  const c=detail.company;
  return <div className="brief-stack">{detail.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented evidence, not the actual operator roster.</p>}<section className="panel prototype-panel"><p className="eyebrow">{c.security_id}</p><h1>{c.company_name??"Unresolved company"}</h1><p>{c.qualified_symbol??"Listing identity unavailable"} · Known at {detail.decision_at}</p>
    <p>{detail.qualifying_result?"Qualifying research result in the unfrozen proposal.":detail.proposed_member?"Proposed member; not a qualifying result.":"Company inspected during roster review; outside the proposal."}</p><p>{c.eligible?"Required evidence checks passed.":`Withheld: ${c.reasons.join("; ")}`}</p>
    {detail.blockers.length>0 && <p className="notice warning">Universe blockers: {detail.blockers.join("; ")}</p>}
    <CalculationView calculation={c.calculation}/></section>
    {c.brief && <AnalystBriefView brief={c.brief}/>}
    <section className="panel prototype-panel"><h2>Size and industry</h2>
      {c.size ? <><p><b>{money(c.size.market_cap_usd)} market cap</b> (band {money(c.size.band_usd[0])}–{money(c.size.band_usd[1])})</p>
        <p><code>{c.size.shares_outstanding.toLocaleString("en-GB")} shares × ${c.size.close.toFixed(2)} close</code></p>
        <p>Shares as of {c.size.shares_as_of} from the {c.size.shares_form} filed {c.size.shares_filed} (accession <code>{c.size.shares_accession}</code>{c.size.share_classes_summed > 1 ? `, ${c.size.share_classes_summed} share counts summed` : ""}); close on {c.size.close_session}, unadjusted. A calculation from stored facts, not a quoted market value.</p></>
        : <p>Market cap unavailable from stored evidence.</p>}
      {c.industry ? <p>SIC {c.industry.sic}: {c.industry.sic_description ?? "no description"} · SEC entity type {c.industry.entity_type ?? "unknown"} · from the stored SEC submissions document retrieved {c.industry.retrieved_at} (<code>{c.industry.response_sha256}</code>)</p>
        : <p>Industry classification unavailable from stored evidence.</p>}
      {(c.sector_notes ?? []).map(note => <p key={note} className="notice">Sector note: {note}</p>)}
    </section>
    {c.valuation && <ValuationView valuation={c.valuation}/>}
    {c.financials && <FinancialHealthView financials={c.financials}/>}
    {c.events && <EventsView events={c.events}/>}
    <section className="panel prototype-panel"><h2>Direct reported evidence and citations</h2><p>Context only — no ranking effect. Stored fact references are not certification of complete financial statements or accounting contexts.</p>
      {c.direct_evidence.map(f=><div className="prototype-fact" key={f.citation.fact_key}><h3>{words(f.field)}{f.reported_days?` · ${f.reported_days}-day reported period`:""}</h3><p><b>{f.value.toLocaleString("en-GB")} {f.unit}</b> · {f.concept}</p><p>{f.period_kind}: {f.reported_start?`${f.reported_start} to `:""}{f.reported_end} · {f.form}</p><p>Public: {f.public_at} · Retrieved: {f.retrieved_at} · Known at: {f.known_at}</p><p>CIK {f.citation.cik} · Accession <code>{f.citation.accession}</code></p><p>Stored fact key <code>{f.citation.fact_key}</code></p><p className="prototype-source">Stored source reference: <code>{f.citation.source_endpoint}</code></p></div>)}
      {c.direct_evidence.length===0 && <p>No usable direct financial facts.</p>}
      <h2>Identity and corporate-action citations</h2>{c.action_coverage?.extension && <p>Corporate-action coverage: stored record through {c.action_coverage.extension.stored_record_through}, extended to the window end by a completed EODHD dividend refresh at {c.action_coverage.extension.dividend_refresh_completed_at}.</p>}{[c.identity_evidence,c.action_coverage].map((item,index)=>item?<dl key={index}>{Object.entries(item).filter(([key])=>key!=="extension").map(([key,value])=><div key={key}><dt>{words(key)}</dt><dd>{value??"Open-ended"}</dd></div>)}</dl>:<p key={index}>{index===0?"No approved full-window listing mapping; identity is checked at the cutoff only.":"Corporate-action coverage unavailable."}</p>)}
    </section>
    <section className="panel prototype-panel"><h2>Risks and missing data</h2><ul>{c.risks.map(r=><li key={r}>{r}</li>)}</ul><ul>{c.missing_data.map(m=><li key={m.field}><b>{words(m.field)}</b>: {m.reasons.join("; ")}</li>)}</ul></section></div>;
}

const ROWS: [string, string, "usd"|"pct"|"x"|"shares"|"eps", boolean][] = [
  ["revenue", "Revenue", "usd", false], ["operating_income", "Operating income", "usd", false], ["net_income", "Net income", "usd", false],
  ["operating_margin", "Operating margin", "pct", true], ["net_margin", "Net margin", "pct", true], ["revenue_growth", "Revenue growth", "pct", true],
  ["operating_cash_flow", "Operating cash flow", "usd", false], ["capital_expenditure", "Capital expenditure", "usd", false],
  ["free_cash_flow", "Free cash flow (OCF − capex)", "usd", true], ["free_cash_flow_margin", "Free cash flow margin", "pct", true],
  ["cash", "Cash and equivalents", "usd", false], ["current_ratio", "Current ratio", "x", true], ["liabilities_to_assets", "Liabilities / assets", "pct", true],
  ["long_term_debt_current", "Long-term debt, current", "usd", false], ["long_term_debt_noncurrent", "Long-term debt, non-current", "usd", false],
  ["equity", "Shareholders' equity", "usd", false], ["diluted_shares", "Diluted shares (weighted)", "shares", false],
  ["diluted_share_change", "Diluted share change", "pct", true], ["diluted_eps", "Diluted EPS", "eps", false],
];
const fmt = (v: number, unit: string) => unit === "usd" ? `${(v / 1e6).toLocaleString("en-GB", {maximumFractionDigits: 0})}M` :
  unit === "pct" ? `${(v * 100).toFixed(1)}%` : unit === "x" ? v.toFixed(2) : unit === "shares" ? `${(v / 1e6).toFixed(1)}M` : v.toFixed(2);
const KINDS: [Observation["kind"], string][] = [["strength", "Strengths"], ["weakness", "Weaknesses"], ["neutral", "Context"], ["gap", "Missing evidence"]];

/** Annual SEC figures with rule-based observations. Calculations are marked with *. */
export function FinancialHealthView({financials}: {financials: Financials}) {
  const years = financials.years;
  const rows = ROWS.filter(([key, , , calc]) => years.some(y => key in (calc ? y.calculated : y.values)));
  return <section className="panel prototype-panel prototype-fin"><h2>Financial health (annual 10-K figures)</h2>
    <p>Rule-based observations from stored SEC filings: interpretation, not a rating, and no effect on the shortlist. {financials.method}</p>
    {KINDS.map(([kind, label]) => { const items = financials.observations.filter(o => o.kind === kind);
      return items.length > 0 && <div key={kind}><h3>{label}</h3><ul>{items.map((o, i) => <li key={i}><b>{o.area}:</b> {o.text}{o.fiscal_years.length > 0 && <small> (fiscal years ending {o.fiscal_years.join(", ")})</small>}</li>)}</ul></div>; })}
    {years.length > 0 ? <div className="prototype-table-wrap"><table><thead><tr><th>USD</th>{years.map(y => <th key={y.fiscal_year_end}>FY{y.fiscal_year_end.slice(0, 4)}<small>to {y.fiscal_year_end}</small></th>)}</tr></thead><tbody>
      {rows.map(([key, label, unit, calc]) => <tr key={key}><td>{label}{calc ? " *" : ""}</td>{years.map(y => { const v = calc ? y.calculated[key] : y.values[key]?.value;
        return <td key={y.fiscal_year_end}>{v === undefined ? "—" : fmt(v, unit)}</td>; })}</tr>)}
    </tbody></table></div> : <p>{financials.tables_omitted ?? "No full-year figures are visible."}{financials.fiscal_years_available !== undefined ? ` (${financials.fiscal_years_available} fiscal years available.)` : ""}</p>}
    {years.length > 0 && <details><summary>Source filings by fiscal year</summary><ul>{years.map(y => <li key={y.fiscal_year_end}>FY {y.fiscal_year_end}: {[...new Set(Object.values(y.values).map(v => `${v.form} ${v.accession}`))].join(" · ")}</li>)}</ul></details>}
    <p>* Calculated from the reported figures. — means not reported or not comparable. Not available: {financials.not_available.join("; ")}.</p>
  </section>;
}

const MULTIPLES: [keyof Valuation["multiples"], string][] = [["price_to_earnings", "Price / earnings"], ["price_to_sales", "Price / sales"],
  ["price_to_free_cash_flow", "Price / free cash flow"], ["price_to_book", "Price / book"]];

/** Current multiples only. No cheap/expensive verdict: that needs sector and own-history context. */
export function ValuationView({valuation}: {valuation: Valuation}) {
  return <section className="panel prototype-panel"><h2>Valuation snapshot</h2>
    <p>{valuation.basis} Fiscal year ending {valuation.fiscal_year_end}; market cap {money(valuation.market_cap_usd)}. Calculated multiples, not a verdict: whether these are low depends on the sector and the company&apos;s own history, which are not assessed yet.</p>
    <dl>{MULTIPLES.filter(([key]) => valuation.multiples[key] !== undefined).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{valuation.multiples[key]!.toFixed(1)}×</dd></div>)}
      {valuation.earnings_yield !== undefined && <div><dt>Earnings yield</dt><dd>{(valuation.earnings_yield * 100).toFixed(1)}%</dd></div>}
      {valuation.free_cash_flow_yield !== undefined && <div><dt>Free cash flow yield</dt><dd>{(valuation.free_cash_flow_yield * 100).toFixed(1)}%</dd></div>}</dl>
    {valuation.not_meaningful.length > 0 && <p>Not meaningful: {valuation.not_meaningful.join("; ")}.</p>}
    {valuation.history && <HistoryView history={valuation.history}/>}
    {valuation.scenarios && <ScenarioView scenarios={valuation.scenarios}/>}
  </section>;
}

function HistoryView({history}: {history: ValuationHistory}) {
  const keys = MULTIPLES.filter(([key]) => history.years.some(y => y.multiples[key] !== undefined));
  return <div className="prototype-fin"><h3>Against its own history</h3>
    <p>{history.basis} A multiple below its usual range can mean the market expects worse results; it is a question to research, not a conclusion.</p>
    {history.comparisons.length > 0 ? <ul>{history.comparisons.map(c => <li key={c.multiple}><b>{c.position} range:</b> {c.text}</li>)}</ul>
      : <p>{history.unavailable ?? "No comparison available."}</p>}
    {history.years.length > 0 && <div className="prototype-table-wrap"><table><thead><tr><th>Fiscal year end</th><th>Close</th>{keys.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead><tbody>
      {history.years.map(y => <tr key={y.fiscal_year_end}><td>{y.fiscal_year_end}<small>price {y.price_session}</small></td><td>${y.close.toFixed(2)}</td>{keys.map(([key]) => <td key={key}>{y.multiples[key] !== undefined ? `${y.multiples[key]!.toFixed(1)}×` : "—"}</td>)}</tr>)}
      {history.current && <tr><td><b>Now (same basis)</b></td><td>${history.current.close.toFixed(2)}</td>{keys.map(([key]) => <td key={key}><b>{history.current!.multiples[key] !== undefined ? `${history.current!.multiples[key]!.toFixed(1)}×` : "—"}</b></td>)}</tr>}
    </tbody></table></div>}
  </div>;
}

/** Stored SEC 8-K/6-K events: potential catalysts and risks. Context only. */
export function EventsView({events}: {events: Events}) {
  return <section className="panel prototype-panel prototype-fin"><h2>Recent filing events (last {events.window_days} days)</h2>
    <p>{events.event_count} SEC event filings. {events.note}{events.latest_known_at ? ` Latest stored retrieval: ${events.latest_known_at}.` : ""}</p>
    {events.results_timing && <p><b>Next results:</b> {events.results_timing.text}</p>}
    {events.flags.length > 0 && <><h3>Flags</h3><ul>{events.flags.map(f => <li key={f.category}><b>{f.kind === "risk" ? "Risk" : "Possible catalyst"}:</b> {f.text} ({f.count})</li>)}</ul></>}
    {events.events.length > 0 && <><h3>Filings</h3><ul>{events.events.map(e => <li key={e.accession + e.category}>
      {e.filing_date} · {e.label}{e.amendment ? " (amendment)" : ""} · {e.form} items {e.items.join(", ") || "none"} · confidence {e.confidence.toFixed(2)}
      {e.url && <> · <a href={e.url} target="_blank" rel="noopener noreferrer">SEC filing</a></>}</li>)}</ul></>}
  </section>;
}

/** One-page summary of the sections below, with counterarguments and gaps. */
export function AnalystBriefView({brief}: {brief: AnalystBrief}) {
  return <section className="panel prototype-panel prototype-fin prototype-brief"><h2>Analyst brief</h2><p>{brief.label}</p>
    {brief.sections.map(section => <div key={section.title}><h3>{section.title}</h3><ul>{section.points.map(point => <li key={point}>{point}</li>)}</ul></div>)}
    <h3>Counterarguments</h3><ul>{brief.counterarguments.map(point => <li key={point}>{point}</li>)}</ul>
    <details><summary>Missing evidence ({brief.missing_evidence.length})</summary><ul>{brief.missing_evidence.map(point => <li key={point}>{point}</li>)}</ul></details>
  </section>;
}

/** Every eligible company side by side, from the same report. Sorted by durable-ID
 *  hash order (the membership order), never by any of these columns. */
export function CompareView({report, companies}: {report: Report; companies: Map<string, Company>}) {
  const [rows, more] = useShown(report.eligible_roster.map(id => companies.get(id)!).filter(Boolean));
  const count = (c: Company, kind: string) => c.financials?.observations.filter(o => o.kind === kind).length ?? 0;
  const position = (c: Company) => { const cs = c.valuation?.history?.comparisons ?? [];
    return cs.length ? `${cs.filter(x => x.position === "below").length}↓ ${cs.filter(x => x.position === "within").length}= ${cs.filter(x => x.position === "above").length}↑` : "—"; };
  return <section className="panel prototype-panel"><h2>Compare eligible companies</h2>
    <p>Same data as each company page, in membership order (not ranked by any column). Vs own history counts multiples below (↓), within (=) and above (↑) the company&apos;s own range. A low multiple or a high momentum figure is a question to research, not a signal to act.</p>
    <div className="prototype-table-wrap"><table><thead><tr><th>Company</th><th>Market cap</th><th>126-session move</th><th>P/E</th><th>FCF yield</th><th>Vs own history</th><th>Health ✓/✗</th><th>Filing risks</th><th>Next results ≈</th></tr></thead><tbody>
      {rows.map(c => <tr key={c.security_id}>
        <td><Link href={detailHref(c.security_id, report.decision_at, report.target_members)}>{c.qualified_symbol}</Link>{report.results.includes(c.security_id) ? " · result" : ""}<small>{c.industry?.sic_description ?? ""}</small></td>
        <td>{c.size ? money(c.size.market_cap_usd) : "—"}</td>
        <td>{c.calculation ? percent(c.calculation.momentum_return) : "—"}</td>
        <td>{c.valuation?.multiples.price_to_earnings !== undefined ? c.valuation.multiples.price_to_earnings.toFixed(1) : "—"}</td>
        <td>{c.valuation?.free_cash_flow_yield !== undefined ? percent(c.valuation.free_cash_flow_yield) : "—"}</td>
        <td>{position(c)}</td>
        <td>{c.financials ? `${count(c, "strength")} / ${count(c, "weakness")}` : "—"}</td>
        <td>{c.events ? c.events.flags.filter(f => f.kind === "risk").length : "—"}</td>
        <td>{c.events?.results_timing?.next_results_estimate ?? "—"}</td>
      </tr>)}
    </tbody></table></div>{more}</section>;
}

export function ScenarioView({scenarios}: {scenarios: Scenarios}) {
  return <div className="prototype-fin"><h3>Scenario range</h3>
    {!scenarios.available ? <p>Not available: {scenarios.reason}</p> : <>
      <p>{scenarios.label} Based on {scenarios.years_used} years of {scenarios.measure} and {scenarios.multiples_used} own {scenarios.multiple_name} values; price ${scenarios.price.toFixed(2)} on {scenarios.price_session}.</p>
      <div className="prototype-table-wrap"><table><thead><tr><th>Case</th><th>Assumption</th><th>Value per share</th><th>Vs price</th></tr></thead><tbody>
        {scenarios.cases!.map(x => <tr key={x.case}><td>{x.case}</td><td>{x.assumption}<small>{(x.profit / 1e6).toLocaleString("en-GB", {maximumFractionDigits: 0})}M USD x {x.multiple.toFixed(1)}</small></td>
          <td>{x.value_per_share === null ? "—" : `$${x.value_per_share.toFixed(2)}`}</td><td>{x.vs_price === null ? (x.note ?? "—") : percent(x.vs_price)}</td></tr>)}
      </tbody></table></div>
      {scenarios.multiple_sensitivity && <p>Sensitivity: at its lowest own {scenarios.multiple_name} ({scenarios.multiple_sensitivity.low_multiple.toFixed(1)}) the middle case would be ${scenarios.multiple_sensitivity.low_value_per_share.toFixed(2)}; at its highest ({scenarios.multiple_sensitivity.high_multiple.toFixed(1)}), ${scenarios.multiple_sensitivity.high_value_per_share.toFixed(2)}.</p>}
      {scenarios.volatility_note && <p className="notice warning">{scenarios.volatility_note}</p>}</>}
    {scenarios.book_value_per_share !== null && <p>Reference: book value ${scenarios.book_value_per_share.toFixed(2)} per share (latest fiscal year end).</p>}
    <p>Ignores debt, cyclicality and structural change. If the business is changing, its own past is a poor guide.</p>
  </div>;
}
