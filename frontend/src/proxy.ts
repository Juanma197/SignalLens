import { timingSafeEqual } from "node:crypto";

import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

function matches(value: string, expected: string): boolean {
  const supplied = Buffer.from(value);
  const target = Buffer.from(expected);
  return (
    supplied.length === target.length &&
    timingSafeEqual(supplied, target)
  );
}

export function proxy(request: NextRequest) {
  const username = process.env.SIGNALLENS_DASHBOARD_USERNAME;
  const password = process.env.SIGNALLENS_DASHBOARD_PASSWORD;

  if (!username || !password) {
    if (process.env.NODE_ENV === "production") {
      return new NextResponse(
        "SignalLens dashboard credentials are not configured.",
        { status: 503 },
      );
    }
    return NextResponse.next();
  }

  const expected = `Basic ${Buffer.from(
    `${username}:${password}`,
  ).toString("base64")}`;
  const supplied = request.headers.get("authorization") ?? "";

  if (!matches(supplied, expected)) {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="SignalLens", charset="UTF-8"',
        "Cache-Control": "no-store",
      },
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: "/((?!_next/static|_next/image|favicon.ico).*)",
};
