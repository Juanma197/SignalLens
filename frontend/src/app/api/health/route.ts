// Railway's deploy health check. Public (the dashboard login does not apply, see
// src/proxy.ts) and reveals nothing: the dashboard itself answers 401 without it.
export function GET() {
  return Response.json({status: "ok"}, {headers: {"Cache-Control": "no-store"}});
}
