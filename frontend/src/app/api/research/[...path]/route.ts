import { NextRequest, NextResponse } from "next/server";

export const DEFAULT_RESEARCH_TIMEOUT_MS = 15_000;
export const MODEL_LABORATORY_TIMEOUT_MS = 60_000;

// Prototype assessments read and fingerprint the full research database
// (about 10-15 s, longer on a busy machine), so they share the long timeout.
export function timeoutForResearchPath(path: string[]) {
  return path[0] === "model-laboratory" || path[0] === "prototype"
    ? MODEL_LABORATORY_TIMEOUT_MS
    : DEFAULT_RESEARCH_TIMEOUT_MS;
}

export async function GET(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  const base = process.env.SIGNALLENS_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(`/api/v1/research/${path.join("/")}`, base);
  target.search = request.nextUrl.search;
  const token = process.env.SIGNALLENS_API_TOKEN;
  try {
    const response = await fetch(target, {cache: "no-store", headers: token ? {Authorization: `Bearer ${token}`} : {}, signal: AbortSignal.timeout(timeoutForResearchPath(path))});
    return new NextResponse(await response.text(), {status: response.status,
      headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch {
    return NextResponse.json({detail: "Research brief service unavailable."}, {status: 503});
  }
}

// Writes are forwarded only for the separate prototype store; the backend also
// refuses them unless SIGNALLENS_PROTOTYPE_WRITES_ENABLED is set.
export function isPrototypeStorePath(path: string[]) {
  return path[0] === "prototype" && path[1] === "store";
}

export async function POST(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  if (!isPrototypeStorePath(path)) {
    return NextResponse.json({detail: {code: "research_write_not_forwarded"}}, {status: 405});
  }
  const base = process.env.SIGNALLENS_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(`/api/v1/research/${path.join("/")}`, base);
  const token = process.env.SIGNALLENS_API_TOKEN;
  const headers: Record<string, string> = {"Content-Type": "application/json"};
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    const response = await fetch(target, {method: "POST", cache: "no-store", headers, body: await request.text(),
      signal: AbortSignal.timeout(timeoutForResearchPath(path))});
    return new NextResponse(await response.text(), {status: response.status,
      headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch {
    return NextResponse.json({detail: {code: "PROTOTYPE_SERVICE_UNAVAILABLE"}}, {status: 503});
  }
}
