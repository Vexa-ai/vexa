/**
 * Get the webapp URL (vexa.ai marketing + billing/account).
 * Used for "Account & Billing" links etc.
 */
export function getWebappUrl(): string {
  return process.env.NEXT_PUBLIC_WEBAPP_URL || "https://vexa.ai";
}

/**
 * Get the full URL for a docs path on the docs site (Mintlify at
 * docs.vexa.ai). Previously this was wrongly rooted at webapp host;
 * fixed 2026-05-01 — `API Docs` in the sidebar pointed at
 * webapp.vexa.ai which is not a docs site.
 */
export function getDocsUrl(path: string): string {
  const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL || "https://docs.vexa.ai";
  // Remove leading slash if present to avoid double slashes
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${docsUrl}${cleanPath}`;
}
