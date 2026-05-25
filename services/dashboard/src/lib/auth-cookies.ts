/**
 * Auth cookie name helpers.
 *
 * The cookie names are configurable via environment variables
 * (VEXA_AUTH_COOKIE_NAME, VEXA_USER_INFO_COOKIE_NAME) so that
 * different deployments can avoid name collisions.
 */

export function getAuthCookieName(): string {
  return process.env.VEXA_AUTH_COOKIE_NAME ?? "vexa-token";
}

export function getUserInfoCookieName(): string {
  return process.env.VEXA_USER_INFO_COOKIE_NAME ?? "vexa-user-info";
}