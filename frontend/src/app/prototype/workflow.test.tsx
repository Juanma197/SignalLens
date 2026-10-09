import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import path from "node:path";
import test from "node:test";
import {renderToStaticMarkup} from "react-dom/server";
import {AnalystBriefView,CompanyView,EventsView,FinancialHealthView,PrototypeNotice,RosterView,ScenarioView,ValuationView,detailHref, type Detail, type Report} from "./view";
import {SnapshotView, ThesisHistory} from "./store-view";

// Actual backend fixture -> CLI/service contract -> shortlist link -> detail view.
// No provider, browser download, credentials or operator database is used.
const project=path.resolve(process.cwd(),"..");
const python=process.env.SIGNALLENS_TEST_PYTHON??path.join(project,".venv",process.platform==="win32"?"Scripts/python.exe":"bin/python");
const folder=mkdtempSync(path.join(tmpdir(),"signallens-prototype-ui-"));
function report(count:number):Report {
  const script=`from pathlib import Path\nimport json\nfrom app.prototype.fixture import create_fixture, DECISION\nfrom app.prototype.service import assess\nr,p=create_fixture(Path(${JSON.stringify(folder)}) / 'fresh-${count}', count=${count})\nprint(json.dumps(assess(research_db=r,production_db=p,decision_at=DECISION)))`;
  return JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
}
const ready=report(18), blocked=report(9);
test.after(()=>rmSync(folder,{recursive:true,force:true}));

test("offline backend roster opens an evidence-rich company detail with identical inputs",()=>{
  const markup=renderToStaticMarkup(<><PrototypeNotice/><RosterView report={ready}/></>);
  assert.match(markup,/UNVALIDATED RESEARCH PROTOTYPE/);
  assert.match(markup,/ZERO VALIDATION CREDIT/);
  assert.match(markup,/SYNTHETIC FIXTURE/);
  assert.match(markup,/Actual eligible roster for review/);
  assert.match(markup,/15 proposed members from 18 eligible/);
  assert.match(markup,/No universe has been frozen/);
  for(const id of ready.results) {
    const company=ready.companies.find(c=>c.security_id===id)!;
    const href=detailHref(id,ready.decision_at,ready.target_members);
    assert.ok(markup.includes(href.replaceAll("&","&amp;")));
    const detail:Detail={...ready,company,proposed_member:true,qualifying_result:true};
    const html=renderToStaticMarkup(<CompanyView detail={detail}/>);
    assert.ok(html.includes(company.calculation!.start_session));
    assert.ok(html.includes(company.calculation!.end_session));
    assert.ok(html.includes(company.direct_evidence[0].citation.accession));
    assert.ok(html.includes(company.direct_evidence[0].citation.fact_key));
    assert.match(html,/no ranking effect/);
    assert.match(html,/Risks and missing data/);
    assert.match(html,/direct_evidence_unavailable/);
    assert.match(html,/126 completed sessions/);
  }
});

test("fewer than ten displays blockers and no result cards",()=>{
  const html=renderToStaticMarkup(<RosterView report={blocked}/>);
  assert.match(html,/eligible population below minimum/);
  assert.match(html,/Requirements remain unchanged/);
  assert.match(html,/No qualifying results/);
  assert.doesNotMatch(html,/Research result 1/);
});

test("stored strings render as inert text and unresolved details retain missing reasons",()=>{
  const company=structuredClone(ready.companies[0]);
  company.company_name='<img src=x onerror=alert(1)>';
  company.eligible=false;company.reasons=['effective_listing_interval_unproven_or_ambiguous'];
  company.calculation=null;company.direct_evidence=[];company.identity_evidence=null;company.action_coverage=null;
  const html=renderToStaticMarkup(<CompanyView detail={{...ready,company,proposed_member:false,qualifying_result:false}}/>);
  assert.ok(html.includes('&lt;img'));
  assert.doesNotMatch(html,/<img/);
  assert.match(html,/126-session calculation withheld/);
  assert.match(html,/identity is checked at the cutoff only/);
  assert.match(html,/effective_listing_interval_unproven_or_ambiguous/);
});

test("a frozen snapshot renders verified membership and pending checkpoints without filled values",()=>{
  const script=`from pathlib import Path\nimport json\nfrom datetime import datetime, timedelta, timezone\nfrom app.prototype.fixture import create_fixture, DECISION\nfrom app.prototype.service import assess\nfrom app.prototype.store import PrototypeStore\nfrom app.prototype.tracking import track\nr,p=create_fixture(Path(${JSON.stringify(folder)}) / 'snapshot')\nstore=PrototypeStore(Path(${JSON.stringify(folder)}) / 'snapshot-store' / 'prototype.duckdb', protected_paths=(r,p))\ns=store.snapshot(store.create_snapshot(assess(research_db=r,production_db=p,decision_at=DECISION), now=DECISION+timedelta(days=1))['snapshot_id'])\nprint(json.dumps({'snapshot':s,'tracking':track(s,research_db=r,now=datetime(2026,12,1,tzinfo=timezone.utc))}, default=str))`;
  const data=JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
  const html=renderToStaticMarkup(<SnapshotView snapshot={data.snapshot} tracking={data.tracking}/>);
  assert.match(html,/Snapshot 2026-10 · frozen/);
  assert.match(html,/Integrity verified/);
  assert.match(html,/SYNTHETIC FIXTURE/);
  assert.match(html,/pending \(0 of 21\)/);
  assert.match(html,/none available \(0 of 15\)/);
  assert.doesNotMatch(html,/NaN|undefined/);
  assert.equal((html.match(/· result/g)??[]).length,3);
});

test("thesis versions render as labelled interpretation and assumptions, newest first",()=>{
  const script=`from pathlib import Path\nimport json\nfrom app.prototype.store import PrototypeStore\ns=PrototypeStore(Path(${JSON.stringify(folder)}) / 'thesis-store' / 'p.duckdb')\ns.add_thesis('x','researching',{'business':'Makes <b>widgets</b>.'})\nprint(json.dumps(s.add_thesis('x','active',{'business':'Makes widgets.','assumptions':'Costs normalise.'})))`;
  const versions=JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
  const html=renderToStaticMarkup(<ThesisHistory versions={versions}/>);
  assert.match(html,/Current: active/);
  assert.match(html,/Business \(interpretation\)/);
  assert.match(html,/Assumptions \(assumption\)/);
  assert.match(html,/1 earlier version/);
  assert.match(html,/Makes &lt;b&gt;widgets&lt;\/b&gt;\./);
  assert.doesNotMatch(html,/Why it might be cheap/);
});

test("shortlist and company pages show market cap and industry with their inputs",()=>{
  const roster=renderToStaticMarkup(<RosterView report={ready}/>);
  assert.match(roster,/\$1\.0\dB market cap · General Industrial Machinery/);
  const company=ready.companies.find(c=>c.security_id===ready.results[0])!;
  const detail:Detail={notice:ready.notice,version:ready.version,decision_at:ready.decision_at,blockers:[],membership_state:ready.membership_state,synthetic_fixture:true,company,proposed_member:true,qualifying_result:true};
  const html=renderToStaticMarkup(<CompanyView detail={detail}/>);
  assert.match(html,/Size and industry/);
  assert.match(html,/10,000,000 shares × \$/);
  assert.match(html,/SIC 3560/);
  assert.match(html,/not a quoted market value/);
});

test("financial health renders annual figures, calculations and grouped observations",()=>{
  const script=`import json, sys\nsys.path.insert(0, 'tests')\nfrom test_prototype_financials import brief, year\nrows=[r for y,rev in zip(range(2022,2026),(100e6,90e6,80e6,70e6)) for r in year(y,rev,-rev*0.1,-rev*0.12,-rev*0.05,rev*0.02,1e6*(1+(y-2022)*0.2))]\nprint(json.dumps(brief(rows), default=str))`;
  const financials=JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
  const html=renderToStaticMarkup(<FinancialHealthView financials={financials}/>);
  assert.match(html,/Financial health \(annual 10-K figures\)/);
  assert.match(html,/Weaknesses/);
  assert.match(html,/Revenue shrank/);
  assert.match(html,/Operating margin \*/);
  assert.match(html,/-10\.0%/);
  assert.match(html,/FY2025<small>to 2025-12-31/);
  assert.match(html,/interpretation, not a rating/);
  assert.doesNotMatch(html,/NaN|undefined/);
});

test("valuation snapshot shows multiples without a verdict",()=>{
  const html=renderToStaticMarkup(<ValuationView valuation={{market_cap_usd:6e8,fiscal_year_end:"2025-12-31",multiples:{price_to_earnings:30,price_to_sales:3},not_meaningful:["Free cash flow is zero or negative"],basis:"Market cap at the decision session against the last full fiscal year; the two dates differ.",earnings_yield:0.0333}}/>);
  assert.match(html,/Price \/ earnings<\/dt><dd>30\.0×/);
  assert.match(html,/Earnings yield<\/dt><dd>3\.3%/);
  assert.match(html,/not a verdict/);
  assert.match(html,/Free cash flow is zero or negative/);
});

test("valuation history renders comparisons, the same-basis current row and its caveat",()=>{
  const script=`import json, sys\nsys.path.insert(0, 'tests')\nfrom datetime import date\nfrom test_prototype_financials import SEC, DECISION, year\nfrom app.prototype.financials import annual_brief, valuation_history\nfrom app.prototype.service import stamp, finite\nrows=[r for y in range(2021,2026) for r in year(y,1000,150,100,160,20,10)]\nfirst=annual_brief(SEC,rows,DECISION,stamp=stamp,finite=finite,revision='first')\nprices={date(y,12,31):{'session':date(y,12,31),'close':100.0+(y-2021)*50} for y in range(2021,2026)}\nprint(json.dumps(valuation_history(first,prices,50.0), default=str))`;
  const history=JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
  const html=renderToStaticMarkup(<ValuationView valuation={{market_cap_usd:5e8,fiscal_year_end:"2025-12-31",multiples:{price_to_earnings:5},not_meaningful:[],basis:"b",history}}/>);
  assert.match(html,/Against its own history/);
  assert.match(html,/below range:<\/b> P\/E 5\.0 vs its own 5-year range 10\.0-30\.0/);
  assert.match(html,/Now \(same basis\)/);
  assert.match(html,/a question to research, not a conclusion/);
});

test("filing events render flags, the cadence estimate and SEC links",()=>{
  const events={window_days:365,event_count:2,counts:{capital_raise:1,earnings_financial_results:1},note:"Categories come from the stored classifier.",latest_known_at:"2026-10-01T00:00:00+00:00",
    flags:[{kind:"risk" as const,category:"capital_raise",count:1,text:"A capital raise or new borrowing was filed in the last year (possible dilution or more debt)."}],
    results_timing:{last_results_filed:"2026-07-30",typical_gap_days:91,next_results_estimate:"2026-10-29",text:"Results were last filed 2026-07-30; at its usual 91-day rhythm the next are likely around 2026-10-29 (estimate from filing cadence, not an announced date)."},
    events:[{accession:"0001065696-26-000041",form:"8-K",filing_date:"2026-07-30",known_at:"x",items:["2.02","9.01"],category:"earnings_financial_results",label:"Earnings or financial results",confidence:0.85,amendment:false,url:"https://www.sec.gov/Archives/edgar/data/1065696/000106569626000041/lkq-20260730.htm"}]};
  const html=renderToStaticMarkup(<EventsView events={events}/>);
  assert.match(html,/Recent filing events \(last 365 days\)/);
  assert.match(html,/<b>Risk:<\/b> A capital raise/);
  assert.match(html,/not an announced date/);
  assert.match(html,/href="https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/1065696\/000106569626000041\/lkq-20260730\.htm" target="_blank" rel="noopener noreferrer"/);
});

test("analyst brief renders sections, counterarguments and missing evidence from the backend",()=>{
  const script=`import json, sys\nsys.path.insert(0, 'tests')\nfrom test_prototype_brief import company, DECISION\nfrom app.prototype.brief import analyst_brief\nprint(json.dumps(analyst_brief(company(0.9,-0.01,-5e6),DECISION,result=True), default=str))`;
  const brief=JSON.parse(execFileSync(python,["-c",script],{cwd:path.join(project,"backend"),encoding:"utf8"}));
  const html=renderToStaticMarkup(<AnalystBriefView brief={brief}/>);
  assert.match(html,/Analyst brief/);
  assert.match(html,/Why it is on the list/);
  assert.match(html,/Counterarguments<\/h3><ul><li>The price is up 90%/);
  assert.match(html,/Missing evidence \(\d+\)/);
  assert.match(html,/not a forecast or a recommendation/);
});

test("comparison table lists every eligible company in membership order with dashes for missing data",()=>{
  const html=renderToStaticMarkup(<RosterView report={ready}/>);
  assert.match(html,/Compare eligible companies/);
  assert.match(html,/not ranked by any column/);
  const table=html.slice(html.indexOf("Compare eligible companies"), html.indexOf("Actual eligible roster for review"));
  const order=[...table.matchAll(/>(SYN\d\d\.US)</g)].map(m=>m[1]);
  assert.deepEqual(order, ready.eligible_roster.map(id=>ready.companies.find(c=>c.security_id===id)!.qualified_symbol));
  assert.match(table,/—/);
});

test("scenario range renders each case, its assumption and the caveats",()=>{
  const html=renderToStaticMarkup(<ScenarioView scenarios={{available:true,measure:"free cash flow",multiple_name:"P/FCF",years_used:5,multiples_used:5,price:100,price_session:"2026-10-07",book_value_per_share:50,label:"Arithmetic from the company's own past results and multiples, assuming that past range is representative. Not a price target or a forecast.",
    cases:[{case:"cautious",assumption:"worst free cash flow of the last 5 years x lowest own P/FCF",profit:8e7,multiple:8,value_per_share:64,vs_price:-0.36},
           {case:"optimistic",assumption:"best free cash flow x median own P/FCF (not the highest)",profit:-1,multiple:10,value_per_share:null,vs_price:null,note:"Free cash flow was zero or negative; no earnings-based value."}]}}/>);
  assert.match(html,/Scenario range/);
  assert.match(html,/Not a price target or a forecast/);
  assert.match(html,/\$64\.00<\/td><td>-36\.00%/);
  assert.match(html,/no earnings-based value/);
  assert.match(html,/book value \$50\.00 per share/);
  assert.match(html,/Ignores debt, cyclicality and structural change/);
  const volatile=renderToStaticMarkup(<ScenarioView scenarios={{available:true,measure:"net income",multiple_name:"P/E",years_used:5,multiples_used:5,price:100,book_value_per_share:null,
    volatility_note:"Net income varied a lot from year to year (coefficient of variation 0.56); treat this range as unreliable.",
    multiple_sensitivity:{low_multiple:3,high_multiple:21,low_value_per_share:39,high_value_per_share:257},
    cases:[{case:"middle",assumption:"median net income x median own P/E",profit:1e8,multiple:10,value_per_share:142,vs_price:0.42}]}}/>);
  assert.match(volatile,/treat this range as unreliable/);
  assert.match(volatile,/at its lowest own P\/E \(3\.0\) the middle case would be \$39\.00/);
});
