import { NextRequest, NextResponse } from "next/server";

// Presence-only check: a UX convenience that avoids flashing a protected
// page before redirecting. It is NOT the security boundary -- every backend
// request independently re-verifies the access_token, and a stale-but-present
// cookie here still gets a real 401 from the API.
const PROTECTED_PREFIXES = ["/settings"];
const AUTH_PAGE_PREFIXES = ["/signin", "/signup", "/forgot-password"];

export function proxy(request: NextRequest) {
  const hasSession =
    request.cookies.has("access_token") || request.cookies.has("refresh_token");
  const { pathname } = request.nextUrl;

  const isProtected = PROTECTED_PREFIXES.some((prefix) => pathname.startsWith(prefix));
  const isAuthPage = AUTH_PAGE_PREFIXES.some((prefix) => pathname.startsWith(prefix));

  if (isProtected && !hasSession) {
    return NextResponse.redirect(new URL("/signin", request.url));
  }

  if (isAuthPage && hasSession) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/settings/:path*", "/signin", "/signup", "/forgot-password"],
};
