import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  const base = process.env.SIGNALLENS_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(`/api/v1/research/${path.join("/")}`, base);
  target.search = request.nextUrl.search;
  const token = process.env.SIGNALLENS_API_TOKEN;
  try {
    const response = await fetch(target, {cache: "no-store", headers: token ? {Authorization: `Bearer ${token}`} : {}, signal: AbortSignal.timeout(15000)});
    return new NextResponse(await response.text(), {status: response.status,
      headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  } catch {
    return NextResponse.json({detail: "Research brief service unavailable."}, {status: 503});
  }
}
