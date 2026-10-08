import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import path from "node:path";
import test from "node:test";
import {renderToStaticMarkup} from "react-dom/server";
import {CompanyView,PrototypeNotice,RosterView,detailHref, type Detail, type Report} from "./view";
import {SnapshotView} from "./store-view";

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
