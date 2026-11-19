/** The Company profile switch's cookies and configuration (ADR-0049), shared by `company/` and `me/`. */

/** The person's own token while their tab works as the company. */
export const PERSON_COOKIE = "vexa-person-token";
/** The person's display info, so the badge can name who to switch back to. */
export const PERSON_INFO_COOKIE = "vexa-person-info";
/** The company token's id, so switching back can revoke it. */
export const COMPANY_TOKEN_ID_COOKIE = "vexa-company-token-id";

/** A Company session lasts a working day; switching back ends it sooner. */
export const COMPANY_TOKEN_SECONDS = 12 * 60 * 60;
/** The admin account addresses this deployment names (comma-separated, as admin-api reads them). */
export function adminAccountAddresses(env: NodeJS.ProcessEnv = process.env): string[] {
  return (env.VEXA_ADMIN_ACCOUNT || "")
    .split(",").map((a) => a.trim().toLowerCase()).filter((a) => a.includes("@"));
}

