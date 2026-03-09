/**
 * Get the webapp URL for API documentation links
 */
export function getWebappUrl(): string {
  return process.env.NEXT_PUBLIC_WEBAPP_URL || "https://vexa.ai";
}

/**
 * Get the full URL for a docs path on docs.vexa.ai
 */
export function getDocsUrl(path?: string): string {
  const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL || "https://docs.vexa.ai";
  if (!path) return docsUrl;
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${docsUrl}${cleanPath}`;
}
