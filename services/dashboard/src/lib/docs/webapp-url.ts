/**
 * Get the webapp URL for API documentation links
 */
export function getWebappUrl(): string {
  if (typeof window !== "undefined") {
    // Client-side: use environment variable or default
    return process.env.NEXT_PUBLIC_WEBAPP_URL || "https://webapp.vexa.ai";
  }
  // Server-side: use environment variable or default
  return process.env.NEXT_PUBLIC_WEBAPP_URL || "https://webapp.vexa.ai";
}

/**
 * Get the full URL for a docs path
 */
export function getDocsUrl(path: string): string {
  const webappUrl = getWebappUrl();
  // Remove leading slash if present to avoid double slashes
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${webappUrl}${cleanPath}`;
}
