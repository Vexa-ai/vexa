export function getAuthCookieName(): string {
  return process.env.VEXA_AUTH_COOKIE_NAME || "vexa-token";
}

export function getUserInfoCookieName(): string {
  return process.env.VEXA_USER_INFO_COOKIE_NAME || "vexa-user-info";
}
