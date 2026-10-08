import Link from "next/link";

export type Calculation = {formula: string; start_session: string; end_session: string; start_adjusted_close: number; end_adjusted_close: number; session_intervals: number; momentum_return: number; source: string[]; latest_input_retrieved_at: string};
export type Fact = {field: string; value: number; unit: string; concept: string; reported_start: string|null; reported_end: string; period_kind: string; reported_days?: number|null; form: string; public_at: string; retrieved_at: string; known_at: string; citation: {fact_key: string; accession: string; cik: string; source_endpoint: string}};
export type Company = {security_id: string; qualified_symbol: string|null; company_name: string|null; eligible: boolean; reasons: string[]; calculation: Calculation|null; direct_evidence: Fact[]; missing_data: {field: string; reasons: string[]}[]; risks: string[]; identity_evidence: Record<string, string|null>|null; action_coverage: Record<string, string>|null};
export type Report = {notice: string; version: string; configuration_hash: string; configuration: {screen: string; membership_order: string; result_order: string; maximum_direct_fact_age_days: number}; decision_at: string; validation_credit: number; membership_state: string; eligible_count: number; minimum_members: number; target_members: number; eligible_roster: string[]; proposed_membership: string[]; results: string[]; companies: Company[]; blockers: string[]; withholding_counts: Record<string, number>; source_schema_states: Record<string,string>; session_calendar: {derived_sessions: number; candidate_dates: number; us_symbols_priced: number; minimum_symbols_per_session: number; last_session: string|null}|null; synthetic_fixture: boolean};
export type Detail = Pick<Report,"notice"|"version"|"decision_at"|"blockers"|"membership_state"|"synthetic_fixture"> & {company: Company; proposed_member: boolean; qualifying_result: boolean};

export const percent = (value: number) => `${(value*100).toFixed(2)}%`;
export const detailHref = (id: string, decision: string, target=15) => `/prototype/company/${encodeURIComponent(id)}?decision_at=${encodeURIComponent(decision)}&target_members=${target}`;
const words = (value: string) => value.replaceAll("_", " ");

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
  return <div className="brief-stack">
    {report.synthetic_fixture && <p className="notice warning">SYNTHETIC FIXTURE — invented companies and evidence. This is not the actual operator roster.</p>}
    <section className="panel prototype-panel"><h2>Proposed research shortlist</h2><p>Known at {report.decision_at}. {report.proposed_membership.length} proposed members from {report.eligible_count} eligible companies; minimum {report.minimum_members}, target {report.target_members}.</p>
      {report.blockers.length > 0 && <p role="status" className="notice warning">Results withheld: {report.blockers.map(words).join(" · ")}. Requirements remain unchanged.</p>}
      {report.results.length === 0 ? <p>No qualifying results at this cutoff.</p> : <div className="prototype-cards">{report.results.map((id,index)=>{const c=companies.get(id)!;return <article key={id}><p className="eyebrow">Research result {index+1}</p><h3>{c.qualified_symbol}</h3><p>{c.company_name}</p><b>{percent(c.calculation!.momentum_return)} over 126 sessions</b><p>Qualified with positive momentum, identity checked at the cutoff, complete price/action coverage and direct financial context.</p><p>{c.risks.join(" ")}</p><Link href={detailHref(id,report.decision_at,report.target_members)}>View calculation and evidence</Link></article>;})}</div>}
    </section>
    <section className="panel prototype-panel"><h2>Actual eligible roster for review</h2><p>All eligible identities are listed below. Proposed membership is the first {report.target_members} in the fixed hash order. No universe has been frozen.</p><p><code>{report.configuration.membership_order}</code></p>
      <div className="prototype-table-wrap"><table><thead><tr><th>Order</th><th>Company / durable ID</th><th>Membership proposal</th><th>Evidence</th></tr></thead><tbody>{report.eligible_roster.map((id,index)=>{const c=companies.get(id)!;return <tr key={id}><td>{index+1}</td><td><Link href={detailHref(id,report.decision_at,report.target_members)}>{c.qualified_symbol} · {c.company_name}</Link><small>{id}</small></td><td>{report.proposed_membership.includes(id)?"Proposed · unfrozen":"Outside bounded proposal"}</td><td>{new Set(c.direct_evidence.map(f=>f.field)).size} direct fields; {c.missing_data.length} missing</td></tr>;})}</tbody></table></div>
      {report.eligible_roster.length===0 && <p>No eligible identities can be established.</p>}
    </section>
    <section className="panel prototype-panel"><h2>Withheld companies and exact blockers</h2><p>Counts may overlap: a company can have several blockers.</p><ul>{Object.entries(report.withholding_counts).map(([reason,count])=><li key={reason}><code>{reason}</code>: {count}</li>)}</ul>
      {report.companies.filter(c=>!c.eligible).map(c=><p key={c.security_id}><Link href={detailHref(c.security_id,report.decision_at,report.target_members)}>{c.qualified_symbol??c.security_id}</Link> — {c.reasons.join("; ")}</p>)}
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
    <section className="panel prototype-panel"><h2>Direct reported evidence and citations</h2><p>Context only — no ranking effect. Stored fact references are not certification of complete financial statements or accounting contexts.</p>
      {c.direct_evidence.map(f=><div className="prototype-fact" key={f.citation.fact_key}><h3>{words(f.field)}{f.reported_days?` · ${f.reported_days}-day reported period`:""}</h3><p><b>{f.value.toLocaleString("en-GB")} {f.unit}</b> · {f.concept}</p><p>{f.period_kind}: {f.reported_start?`${f.reported_start} to `:""}{f.reported_end} · {f.form}</p><p>Public: {f.public_at} · Retrieved: {f.retrieved_at} · Known at: {f.known_at}</p><p>CIK {f.citation.cik} · Accession <code>{f.citation.accession}</code></p><p>Stored fact key <code>{f.citation.fact_key}</code></p><p className="prototype-source">Stored source reference: <code>{f.citation.source_endpoint}</code></p></div>)}
      {c.direct_evidence.length===0 && <p>No usable direct financial facts.</p>}
      <h2>Identity and corporate-action citations</h2>{[c.identity_evidence,c.action_coverage].map((item,index)=>item?<dl key={index}>{Object.entries(item).map(([key,value])=><div key={key}><dt>{words(key)}</dt><dd>{value??"Open-ended"}</dd></div>)}</dl>:<p key={index}>{index===0?"No approved full-window listing mapping; identity is checked at the cutoff only.":"Corporate-action coverage unavailable."}</p>)}
    </section>
    <section className="panel prototype-panel"><h2>Risks and missing data</h2><ul>{c.risks.map(r=><li key={r}>{r}</li>)}</ul><ul>{c.missing_data.map(m=><li key={m.field}><b>{words(m.field)}</b>: {m.reasons.join("; ")}</li>)}</ul></section></div>;
}
