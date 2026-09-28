import { NextRequest, NextResponse } from "next/server";

async function forward(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  const base = process.env.SIGNALLENS_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(`/api/v1/operations/${path.join("/")}`, base);
  target.search = request.nextUrl.search;
  const headers: Record<string, string> = {"Content-Type": "application/json"};
  const apiToken = process.env.SIGNALLENS_API_TOKEN;
  if (apiToken) headers.Authorization = `Bearer ${apiToken}`;
  const operationToken = request.headers.get("X-SignalLens-Operation-Authorization");
  if (operationToken) headers["X-SignalLens-Operation-Authorization"] = operationToken;
  try {
    const response = await fetch(target, {method: request.method, headers,
      body: request.method === "GET" ? undefined : await request.text(), cache: "no-store"});
    return new NextResponse(await response.text(), {status: response.status,
      headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch {
    return NextResponse.json({detail: {code: "operator_api_unavailable",
      message: "The research operator API is unavailable."}}, {status: 503});
  }
}

export const GET = forward;
export const POST = forward;
