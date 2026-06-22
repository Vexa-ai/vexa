/**
 * The auth cookie names, read from env so a deployment can rename them without a rebuild.
 * The login flow sets `vexa-token` (the api key) + `vexa-user-info`; the server routes read them.
 */
export function getAuthCookieName(): string {
  return process.env.VEXA_AUTH_COOKIE_NAME || "vexa-token";
}

export function getUserInfoCookieName(): string {
  return process.env.VEXA_USER_INFO_COOKIE_NAME || "vexa-user-info";
}
