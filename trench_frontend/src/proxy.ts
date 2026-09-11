import { NextRequest, NextResponse } from "next/server";

// Presence-only check: a UX convenience that avoids flashing a protected
// page before redirecting. It is NOT the security boundary -- every backend
// request independently re-verifies the access_token, and a stale-but-present
// cookie here still gets a real 401 from the API.
//
// Cookie names must match ACCESS_TOKEN_COOKIE/REFRESH_TOKEN_COOKIE in
// src/lib/api.ts -- these are duplicated (not imported) because that module
// pulls in axios/js-cookie, which don't belong in the edge runtime here.
const PROTECTED_PREFIXES = ["/settings", "/chat", "/documents", "/organization"];
const AUTH_PAGE_PREFIXES = ["/signin", "/signup", "/forgot-password"];

export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("a_token") || request.cookies.has("r_token");
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
  matcher: [
    "/settings/:path*",
    "/chat/:path*",
    "/documents/:path*",
    "/organization/:path*",
    "/signin",
    "/signup",
    "/forgot-password",
  ],
};
