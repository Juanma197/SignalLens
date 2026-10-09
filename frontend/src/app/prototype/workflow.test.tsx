import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import path from "node:path";
import test from "node:test";
import {renderToStaticMarkup} from "react-dom/server";
import {AnalystBriefView,CompanyView,EventsView,FinancialHealthView,PrototypeNotice,RosterView,ScenarioView,ValuationView,detailHref, type Detail, type Report} from "./view";
import {SnapshotView, ThesisHistory} from "./store-view";
import {ValueRankingView, type ValueRanking} from "./value-view";
import {PortfolioView, ReassessmentNotice, type Portfolio} from "./portfolio-view";
import {ChecksOverview, type ChecksReport} from "./checks-view";
import {MonthlyView, type Monthly} from "./monthly-view";
import {AllocationView, type Allocation} from "./monthly-view";
import {ScorecardView, type Scorecard} from "./scorecard-view";
import {WhenPicker, cutoffFor, describeCutoff, friendlyError, todayUtc, whenFromQuery} from "./when";

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

test("value ranking shows picks with reasons and never forces empty places",()=>{
  // The synthetic fixture has no annual figures, so nothing is assessable and nothing is picked.
  assert.ok(ready.value_ranking);
  const none=renderToStaticMarkup(<ValueRankingView ranking={ready.value_ranking!} decision={ready.decision_at} target={ready.target_members}/>);
  assert.match(none,/No company passes the filters this month/);
  assert.match(none,/Not assessable/);
  const pick={security_id:"a",qualified_symbol:"AAA.US",company_name:"<b>A</b>",status:"candidate" as const,reasons:[],rank:1,recommendation:"strong_buy" as const,upside:0.5,
    cautious_vs_price:0.05,optimistic_vs_price:0.9,measure:"net income",price:10,price_session:"2026-10-08",conviction:"high" as const,conviction_points:5,
    conviction_for:["Even the cautious case is at or above the price (margin of safety)."],conviction_against:[],risk:"low" as const,risks:[],score:0.5};
  const ranking:ValueRanking={population:3,picks:["a"],method:"m.",label:"Unvalidated.",rules:{minimum_upside:0.15,strong_upside:0.3,maximum_picks:3},
    companies:[pick,{security_id:"t",qualified_symbol:"TRAP.US",company_name:null,status:"value_trap",reasons:["Net loss in the latest fiscal year."],rank:null,upside:2}]};
  const html=renderToStaticMarkup(<ValueRankingView ranking={ranking} decision="2026-10-09T00:00:00Z" target={15}/>);
  assert.match(html,/#1 · top opportunity/);
  assert.match(html,/Strong Buy/);
  assert.match(html,/\+50\.00%/);
  assert.match(html,/margin of safety/);
  assert.match(html,/Value trap — excluded/);
  assert.match(html,/Net loss in the latest fiscal year/);
  assert.match(html,/Only 1 company qualifies/);
  assert.match(html,/Only 3 companies could be assessed/);
  assert.doesNotMatch(html,/<b>A<\/b>/);
});

test("portfolio shows priced and unpriced holdings honestly and keeps voided trades visible",()=>{
  const base={currency:"USD",realised_profit:0,fees:0,first_traded_on:"2026-09-01",last_traded_on:"2026-09-01",trades:1};
  const portfolio:Portfolio={as_of:"2026-10-09T12:00:00+00:00",method:"average cost",currencies:["USD"],
    positions:[{...base,qualified_symbol:"SYN01.US",company_name:null,listed_name:"Synthetic company 01",security_id:"synthetic-01",shares:10,cost_basis:1000,average_cost:100,
      price:{close:120,trading_date:"2026-09-30"},market_value:1200,unrealised_profit:200,unrealised_return:0.2,weight:1},
      {...base,qualified_symbol:"NOPE.US",company_name:"<img src=x onerror=alert(1)>",security_id:null,shares:3,cost_basis:60,average_cost:20,price:null,market_value:null,unrealised_profit:null,unrealised_return:null,weight:null}],
    closed_positions:[],
    totals:[{currency:"USD",positions:2,priced_positions:1,cost_basis:1060,priced_cost_basis:1000,market_value:1200,unrealised_profit:200,unrealised_return:0.2,realised_profit:0}],
    transactions:[{transaction_id:"a",kind:"buy",qualified_symbol:"SYN01.US",company_name:null,shares:10,price:100,fees:0,currency:"USD",traded_on:"2026-09-01",note:null,recorded_at:"",voided_at:null,void_reason:null},
      {transaction_id:"b",kind:"buy",qualified_symbol:"SYN01.US",company_name:null,shares:10,price:100,fees:0,currency:"USD",traded_on:"2026-09-01",note:null,recorded_at:"",voided_at:"2026-10-09T00:00:00+00:00",void_reason:"Entered twice."}],
    account_currency:"GBP",settings:{monthly_contribution:250,max_holdings:10,fractional_shares:true,currency:"GBP",is_default:false,recorded_at:"2026-10-01T00:00:00+00:00"},
    cash:{currency:"GBP",balance:195,overdrawn:false,deposited:235,withdrawn:0,spent_on_buys:150,received_from_sales:110,deposited_this_month:0,
      uncounted_trades:[{transaction_id:"a",qualified_symbol:"SYN01.US",traded_on:"2026-09-01"}],method:"Confirmed deposits.",
      entries:[{on:"2026-09-15",kind:"sell",amount:110,balance:195,transaction_id:"s",qualified_symbol:"OLD.US"},{on:"2026-09-04",kind:"buy",amount:-150,balance:85,transaction_id:"x",qualified_symbol:"NEW.US"},
        {on:"2026-09-01",kind:"deposit",amount:200,balance:235,movement_id:"m2",note:"September"},{on:"2026-08-20",kind:"deposit",amount:35,balance:35,movement_id:"m1"}]}};
  const html=renderToStaticMarkup(<PortfolioView portfolio={portfolio} cutoff="2026-10-02T12:00:00Z" today="2026-10-09" busy={false} onTrade={async()=>true} onVoid={()=>{}}
    onCash={async()=>true} onVoidCash={()=>{}} onSettings={async()=>true}/>);
  assert.match(html,/\$1,200\.00/);
  assert.match(html,/1 of 2 holdings priced; unpriced holdings are excluded, not estimated/);
  assert.match(html,/no stored price/);
  assert.match(html,/not covered by SignalLens data/);
  assert.match(html,/20\.00%/);
  assert.match(html,/voided: Entered twice\./);
  // One live trade and two deposits can be voided; buys and sales in the cash list are voided as trades.
  assert.equal((html.match(/>Void</g)??[]).length,3);
  assert.match(html,/Available cash · GBP/);
  assert.match(html,/£195\.00/);
  assert.match(html,/Sale OLD\.US/);
  assert.match(html,/Planned contribution £250\.00/);
  assert.match(html,/2 \/ 10/);
  assert.match(html,/1 earlier trade has no GBP total/);
  assert.match(html,/value="250"/);
  assert.match(html,/Total paid in GBP/);
  assert.match(html,/buy part of a share \(Trading 212 does\)/);
  assert.ok(html.includes(detailHref("synthetic-01","2026-10-02T12:00:00Z").replaceAll("&","&amp;")));
  assert.doesNotMatch(html,/<img/);
  assert.match(html,/SignalLens never places orders/);
});

test("thesis checks show broken conditions first, automatic signs and uncovered holdings",()=>{
  const report:ChecksReport={decision_at:"2026-10-09T00:00:00+00:00",method:"Re-evaluated.",synthetic_fixture:false,metrics:[],uncovered_holdings:["MSFT.US"],companies:[
    {security_id:"a",qualified_symbol:"AAA.US",company_name:"<i>A</i>",overall:"broken",interest:["held"],fiscal_year_end:"2025-12-31",has_own_checks:true,
      checks:[{metric:"operating_margin",comparator:"at_least",threshold:0.1,note:"Pricing power.",label:"Operating margin",unit:"fraction",value:0.072,status:"broken",reason:null,fiscal_year_end:"2025-12-31"},
        {metric:"free_cash_flow",comparator:"at_least",threshold:0,note:null,label:"Free cash flow",unit:"usd",value:null,status:"unknown",reason:"No visible value at this cutoff.",fiscal_year_end:null}],
      automatic:[{kind:"filing_risk",severity:"warning",text:"Auditor change."}]},
    {security_id:"b",qualified_symbol:"BBB.US",company_name:null,overall:"intact",interest:["watched"],has_own_checks:false,checks:[],automatic:[]}]};
  const html=renderToStaticMarkup(<ChecksOverview report={report} target="15"/>);
  assert.match(html,/1 broken · 0 warning · 0 not fully checkable · 1 intact/);
  assert.match(html,/Thesis broken/);
  assert.match(html,/Operating margin at least 10%: now <b>7\.2%<\/b>/);
  assert.match(html,/Pricing power\./);
  assert.match(html,/No visible value at this cutoff\./);
  assert.match(html,/Auditor change\..*Automatic check/);
  assert.match(html,/Held but outside SignalLens data, so not checked: MSFT\.US/);
  assert.match(html,/no conditions of your own yet/);
  assert.doesNotMatch(html,/<i>A<\/i>/);
});

test("monthly view lists picks, held flags and a reasoned decision per holding",()=>{
  const evidence={upside:null,weight:null,thesis:"not_covered",value_status:null,conviction:null,risk:null};
  const monthly:Monthly={decision_at:"2026-10-09T00:00:00+00:00",notice:"",synthetic_fixture:false,target_members:15,population:7,
    picks:[{security_id:"l",qualified_symbol:"LKQ.US",company_name:"LKQ Corporation",status:"candidate",reasons:[],rank:1,recommendation:"buy",upside:1.3,conviction:"medium",risk:"low",held:true}],
    holdings:[{qualified_symbol:"EGY.US",security_id:"e",company_name:"Vaalco",currency:"USD",shares:200,average_cost:6,cost_basis:1200,price:{close:4,trading_date:"2026-10-08"},market_value:800,unrealised_return:-0.33,weight:0.4,
        checks:null,decision:"SELL",reasons:["The thesis is broken:","Net loss in the latest fiscal year."],evidence:{...evidence,upside:-0.36,weight:0.4,thesis:"broken"}},
      {qualified_symbol:"MSFT.US",security_id:null,company_name:"Microsoft",currency:"USD",shares:5,average_cost:410,cost_basis:2050,price:null,market_value:null,unrealised_return:null,weight:null,
        checks:null,decision:"REVIEW",reasons:["SignalLens has no evidence for this holding; decide from your own research."],evidence}],
    totals:[],counts:{"SELL":1,"REDUCE":0,"REVIEW":1,"BUY MORE":0,"HOLD":0},method:"Fixed rules.",label:"Decision support only. Nothing is executed.",
    rules:{buy_more_minimum_upside:0.15,maximum_position_weight_for_buying:0.25,reduce_above_position_weight:0.35,sell_below_upside:-0.2,reduce_below_upside:0}};
  const html=renderToStaticMarkup(<MonthlyView monthly={monthly}/>);
  assert.match(html,/SELL 1/);
  assert.match(html,/BUY MORE 0/);
  assert.match(html,/LKQ Corporation · already held/);
  assert.match(html,/\+130\.00%/);
  assert.match(html,/Net loss in the latest fiscal year\./);
  assert.match(html,/decide from your own research/);
  assert.match(html,/no stored price/);
  assert.match(html,/Nothing is executed/);
  assert.match(html,/SELL when the thesis breaks or the price is 20\.00% above/);
});

test("allocation lists sales then buys, the cash left and never executes",()=>{
  const allocation:Allocation={new_cash:1000,reinvest_sales:true,sale_proceeds:800,available:1800,invested:1500,left_as_cash:300,portfolio_after:9000,
    rules:{position_limit:0.25,minimum_purchase_usd:50},method:"Sales first.",label:"Nothing is executed.",
    sales:[{action:"SELL",qualified_symbol:"EGY.US",security_id:"e",company_name:"Vaalco",shares:200,price:4,amount:800,why:"Sell the whole position."}],
    buys:[{action:"NEW BUY",qualified_symbol:"GPI.US",security_id:"g",company_name:"Group 1",shares:6,price:236.61,amount:1419.66,weight_after:0.158,why:"Open: one of this month's top picks."}]};
  const html=renderToStaticMarkup(<AllocationView allocation={allocation} decision="2026-10-09T00:00:00Z" target={15}/>);
  assert.match(html,/\$1,000\.00.{0,20}new money/);
  assert.match(html,/\$1,800\.00/);
  assert.match(html,/\$300\.00/);
  assert.ok(html.indexOf("EGY.US")<html.indexOf("GPI.US"));
  assert.match(html,/15\.80%/);
  assert.match(html,/Nothing is executed/);
  const none=renderToStaticMarkup(<AllocationView allocation={{...allocation,sales:[],buys:[]}} decision="2026-10-09T00:00:00Z" target={15}/>);
  assert.match(none,/Holding cash is a valid outcome/);
  const pooled=renderToStaticMarkup(<AllocationView decision="2026-10-09T00:00:00Z" target={15} allocation={{...allocation,max_holdings:10,holdings_after:10,
    skipped_no_slot:[{qualified_symbol:"WAIT.US",security_id:"w",company_name:"Waiting"}],
    buys:[{...allocation.buys[0],shares:0.1234,amount_gbp:1135.73}],
    account:{currency:"GBP",cash_pool:640,overdrawn:false,uncounted_trades:0,deposited_this_month:0,monthly_contribution:200,contribution_included:0,available:640,
      gbp_per_usd:{rate:0.8,observed_on:"2026-10-08",source:"stored"},sale_proceeds:640,invested:1200,left_as_cash:80}}}/>);
  assert.match(pooled,/£640\.00 in the cash pool/);
  assert.match(pooled,/>0\.1234</);
  assert.match(pooled,/£1,135\.73/);
  assert.match(pooled,/£80\.00<\/b> stays as cash for later/);
  assert.match(pooled,/contribution isn&#x27;t recorded yet/);
  assert.match(pooled,/0\.8000 per dollar \(stored rate, 2026-10-08\)/);
  assert.match(pooled,/10 of at most 10/);
  assert.match(pooled,/No free place for WAIT\.US/);
  const noRate=renderToStaticMarkup(<AllocationView decision="2026-10-09T00:00:00Z" target={15} allocation={{...allocation,
    account:{currency:"GBP",cash_pool:-5,overdrawn:true,uncounted_trades:2,deposited_this_month:200,monthly_contribution:200,contribution_included:0,available:0,
      gbp_per_usd:null,sale_proceeds:null,invested:null,left_as_cash:0}}}/>);
  assert.match(noRate,/no purchases can be sized yet/);
  assert.match(noRate,/below zero/);
  assert.match(noRate,/2 trade\(s\) have no GBP total/);
  assert.doesNotMatch(noRate,/recorded yet/);
});

test("scorecard shows hit rates, pending checkpoints and the empty state",()=>{
  type Point=Scorecard["months"][number]["items"][number]["checkpoints"][number];
  const point=(s:number,extra:Omit<Point,"sessions">):Point=>({sessions:s,...extra});
  const card:Scorecard={checkpoints:[21,63],groups:{pick:"Top picks",sell_or_reduce:"Sell or reduce"},benchmark:"Equal-weight.",rule:"Beat it.",label:"Not validation.",latest_stored_session:"2026-11-02",
    summary:[{group:"pick",sessions:21,scored:2,right:1,hit_rate:0.5,mean_excess:0.03},{group:"pick",sessions:63,scored:0,right:0,hit_rate:null,mean_excess:null},
      {group:"sell_or_reduce",sessions:21,scored:1,right:1,hit_rate:1,mean_excess:-0.05},{group:"sell_or_reduce",sessions:63,scored:0,right:0,hit_rate:null,mean_excess:null}],
    months:[{record_id:"r",month:"2026-10",decision_at:"2026-10-09T00:00:00Z",base_session:"2026-10-08",benchmark:[{sessions:21,return:0.02,available:7,of:7},{sessions:63,return:null,available:0,of:7}],
      items:[{kind:"pick",qualified_symbol:"LKQ.US",decision:null,recommendation:"buy",rank:1,group:"pick",checkpoints:[point(21,{status:"available",return:0.08,excess:0.06,right:true}),point(63,{status:"pending",sessions_elapsed:21})]},
        {kind:"holding",qualified_symbol:"MSFT.US",decision:"REVIEW",group:null,checkpoints:[point(21,{status:"missing_price"}),point(63,{status:"pending",sessions_elapsed:21})]}]}]};
  const html=renderToStaticMarkup(<ScorecardView card={card}/>);
  assert.match(html,/1 of 2 right vs list \(50\.00%\)/);
  assert.match(html,/none scored yet/);
  assert.match(html,/Pick #1 · Buy/);
  assert.match(html,/✓ \+6\.00% vs list/);
  assert.match(html,/once index-fund prices are downloaded/);
  const withMarket:Scorecard={...card,market:"S&P 500 (SPY)",
    summary:card.summary.map(x=>x.group==="pick"&&x.sessions===21?{...x,market_scored:2,market_right:0,market_hit_rate:0,mean_excess_market:-0.04}:x),
    months:card.months.map(m=>({...m,funds:[{qualified_symbol:"SPY.US",label:"S&P 500 (SPY)",checkpoints:[{sessions:21,return:0.05},{sessions:63,return:null}]}],
      items:m.items.map(i=>({...i,checkpoints:i.checkpoints.map(p=>p.status==="available"?{...p,excess_market:0.03,right_market:true}:p)}))}))};
  const market=renderToStaticMarkup(<ScorecardView card={withMarket}/>);
  assert.match(market,/0 of 2 right vs S&amp;P 500 · average -4\.00%/);
  assert.match(market,/✓ \+3\.00% vs S&amp;P 500/);
  assert.match(market,/S&amp;P 500 \(SPY\).*\+5\.00%/);
  assert.match(html,/pending \(21\/63\)/);
  assert.match(html,/REVIEW · not scored/);
  assert.match(html,/~1 month/);
  assert.match(renderToStaticMarkup(<ScorecardView card={{...card,months:[]}}/>),/No recorded months yet/);
});

test("dates are chosen as Latest or a past day, never typed as timestamps",()=>{
  assert.deepEqual(whenFromQuery(null),{mode:"latest"});
  assert.deepEqual(whenFromQuery("not a date"),{mode:"latest"});
  assert.deepEqual(whenFromQuery("2026-09-15T13:00:00Z"),{mode:"date",date:"2026-09-15"});
  assert.deepEqual(whenFromQuery(new Date().toISOString()),{mode:"latest"});
  assert.equal(cutoffFor({mode:"date",date:"2026-09-15"}),"2026-09-15T23:59:59Z");  // after that day's US close
  const now=Date.now(), latest=new Date(cutoffFor({mode:"latest"})).getTime();
  assert.ok(Math.abs(latest-now)<5000 && /Z$/.test(cutoffFor({mode:"latest"})));
  assert.equal(new Date(cutoffFor({mode:"date",date:todayUtc()})).toISOString().slice(0,10),todayUtc());  // today means now
  assert.equal(describeCutoff("2026-10-09T12:05:00Z"),"9 Oct 2026, 12:05 UTC");
  assert.match(friendlyError("research_maintenance"),/being updated/);
  assert.equal(friendlyError("SOMETHING_NEW"),"SOMETHING_NEW");
  const html=renderToStaticMarkup(<WhenPicker when={{mode:"date",date:"2026-09-15"}} onChange={()=>{}}/>);
  assert.match(html,/Show data as of/); assert.match(html,/type="date"/); assert.doesNotMatch(html,/ISO timestamp/);
});

test("recording a sale or deposit says whether a Telegram reassessment follows",()=>{
  assert.match(renderToStaticMarkup(<ReassessmentNotice status="scheduled"/>),/Reassessing your cash now\. You&#x27;ll get a Telegram message only if it has a worthwhile use/);
  assert.match(renderToStaticMarkup(<ReassessmentNotice status="telegram_not_configured"/>),/Telegram isn&#x27;t set up/);
  assert.equal(renderToStaticMarkup(<ReassessmentNotice/>),"");
});
