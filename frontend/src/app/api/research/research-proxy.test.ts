import assert from "node:assert/strict";
import test from "node:test";
import {setTimeout as delay} from "node:timers/promises";
import {NextRequest} from "next/server";
import {GET, MODEL_LABORATORY_TIMEOUT_MS, timeoutForResearchPath} from "./[...path]/route";

test("a response taking more than 15 seconds succeeds within the Model Laboratory timeout",async t=>{
  const originalFetch=globalThis.fetch;
  t.after(()=>{globalThis.fetch=originalFetch});
  globalThis.fetch=async(_input,init)=>{
    await delay(15_050,undefined,{signal:init?.signal as AbortSignal});
    return json({ok:true});
  };
  const response=await GET(new NextRequest("https://frontend.example/api/research/model-laboratory/preview"),{params:Promise.resolve({path:["model-laboratory","preview"]})});
  assert.equal(response.status,200);
  assert.equal(MODEL_LABORATORY_TIMEOUT_MS,60_000);
  assert.equal(timeoutForResearchPath(["briefs"]),15_000);
});

test("API authentication is forwarded to the Model Laboratory backend",async t=>{
  const originalFetch=globalThis.fetch;
  const originalUrl=process.env.SIGNALLENS_API_URL;
  const originalToken=process.env.SIGNALLENS_API_TOKEN;
  t.after(()=>{globalThis.fetch=originalFetch; if(originalUrl===undefined)delete process.env.SIGNALLENS_API_URL;else process.env.SIGNALLENS_API_URL=originalUrl;if(originalToken===undefined)delete process.env.SIGNALLENS_API_TOKEN;else process.env.SIGNALLENS_API_TOKEN=originalToken});
  process.env.SIGNALLENS_API_URL="https://backend.example";
  process.env.SIGNALLENS_API_TOKEN="test-token";
  let authorization:string|null=null;
  globalThis.fetch=async(_input,init)=>{authorization=new Headers(init?.headers).get("Authorization");return json({ok:true})};
  const response=await GET(new NextRequest("https://frontend.example/api/research/model-laboratory/preview?decision_at=x"),{params:Promise.resolve({path:["model-laboratory","preview"]})});
  assert.equal(response.status,200);
  assert.equal(authorization,"Bearer test-token");
});

const json=(body:unknown)=>new Response(JSON.stringify(body),{headers:{"Content-Type":"application/json"}});
