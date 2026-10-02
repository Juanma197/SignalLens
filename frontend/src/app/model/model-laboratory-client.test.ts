import assert from "node:assert/strict";
import test from "node:test";
import {LaboratoryState, loadModelLaboratory, safeDecisionTimestamp} from "./model-laboratory-client";

const report={labels:["INDICATIVE"],decision_at:"2026-10-01T00:00:00.000Z",model_version:"test",configuration_hash:"hash",registration_timestamp:"registered",eligible_universe_count:3,scored_count:3,withheld_count:0,preview_entries:[],prospective_vintages_created:0,validation_observations_added:0,recommendations_generated:0};
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}});

test("preview success remains available when reconstruction fails",async()=>{
  const states:LaboratoryState[]=[];
  let calls=0;
  await loadModelLaboratory("2026-10-01T12:00",state=>states.push(state),async()=>++calls===1?json(report):json({},503));
  assert.equal(states.at(-1)?.preview.status,"available");
  assert.equal(states.at(-1)?.reconstruction.status,"error");
  assert.deepEqual(states.at(-1)?.preview.report,report);
});

test("an unavailable reconstruction is not reported as a preview failure",async()=>{
  const states:LaboratoryState[]=[];
  let calls=0;
  await loadModelLaboratory("2026-10-01T12:00",state=>states.push(state),async()=>++calls===1?json(report):json({reconstruction_status:"unavailable"}));
  assert.equal(states.at(-1)?.preview.status,"available");
  assert.equal(states.at(-1)?.reconstruction.status,"unavailable");
});

test("malformed and empty timestamps fail safely without making a request",async()=>{
  for (const value of ["","not-a-date"]) {
    let calls=0;
    const states:LaboratoryState[]=[];
    await loadModelLaboratory(value,state=>states.push(state),async()=>{calls++;return json(report)});
    assert.equal(calls,0);
    assert.equal(states.at(-1)?.preview.status,"error");
  }
  assert.equal(safeDecisionTimestamp("not-a-date"),null);
});
