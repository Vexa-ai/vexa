/**
 * Shared cookie options for vexa-token and related cookies.
 * When COOKIE_DOMAIN is set (e.g., ".vexa.ai"), cookies are shared across subdomains for SSO.
 */
export function getVexaCookieOptions(overrides?: Record<string, unknown>) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    maxAge: 60 * 60 * 24 * 30, // 30 days
    path: "/",
    ...(process.env.COOKIE_DOMAIN ? { domain: process.env.COOKIE_DOMAIN } : {}),
    ...overrides,
  };
}
