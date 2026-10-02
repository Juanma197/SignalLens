import { NextRequest, NextResponse } from "next/server";

export const DEFAULT_RESEARCH_TIMEOUT_MS = 15_000;
export const MODEL_LABORATORY_TIMEOUT_MS = 60_000;

export function timeoutForResearchPath(path: string[]) {
  return path[0] === "model-laboratory"
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
