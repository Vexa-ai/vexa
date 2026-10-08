/** What an OAuth provider actually vouches for, decided before any account is looked up.
 *
 *  An account here is keyed by its email address, so an OAuth sign-in may use the provider's email
 *  only when the provider has verified that the person holds that mailbox:
 *
 *  - **Google** — the ID token's `email_verified` must be exactly `true`.
 *  - **Microsoft** — the claims come from the ID token the token endpoint returned (`account.id_token`,
 *    received directly over TLS from Microsoft). With `MICROSOFT_TENANT_ID` set to a tenant id (a
 *    GUID), the token's `tid` must be that tenant: the tenant's own directory vouches for its users.
 *    With a multi-tenant authority (`common`, `organizations`, `consumers`, or unset), any tenant's
 *    administrator can put any address in `email`, so the token must also carry `xms_edov: true`
 *    (the optional claim that says the email's domain is verified by its tenant), or the sign-in is
 *    refused. A tenant named by domain rather than id is single-tenant at the authority and is not
 *    compared again here.
 *
 *  The stable subject (`google:<sub>`, `microsoft:<tid>:<oid>`) is returned with the email for logging
 *  and for binding an account to it. Binding is not enforced yet: admin-api has no route that stores a
 *  provider subject on a user, so an account is still found by its (verified) email alone.
 */

export type ProviderIdentity =
  | { ok: true; email: string; subject: string }
  | { ok: false; why: string };

const MULTI_TENANT = new Set(["", "common", "organizations", "consumers"]);
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Claims = Record<string, unknown>;

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** The payload of a JWT, without verifying its signature — callers use it only for a token the
 *  provider's token endpoint returned to this server directly. */
export function jwtClaims(token: unknown): Claims | null {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length < 2 || !parts[1]) return null;
  try {
    const json = Buffer.from(parts[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
    const claims = JSON.parse(json);
    return claims && typeof claims === "object" && !Array.isArray(claims) ? (claims as Claims) : null;
  } catch {
    return null;
  }
}

function emailOf(claims: Claims): string | null {
  const email = str(claims.email).toLowerCase();
  return EMAIL.test(email) ? email : null;
}

function google(profile: Claims | undefined): ProviderIdentity {
  if (!profile) return { ok: false, why: "no profile from Google" };
  if (profile.email_verified !== true) return { ok: false, why: "Google has not verified this email address" };
  const email = emailOf(profile);
  const sub = str(profile.sub);
  if (!email || !sub) return { ok: false, why: "Google returned no email or subject" };
  return { ok: true, email, subject: `google:${sub}` };
}

function microsoft(account: Claims | null | undefined, env: Record<string, string | undefined>): ProviderIdentity {
  const claims = jwtClaims(account?.id_token);
  if (!claims) return { ok: false, why: "no ID token from Microsoft" };
  const tid = str(claims.tid).toLowerCase();
  const oid = str(claims.oid).toLowerCase();
  if (!tid || !oid) return { ok: false, why: "the Microsoft ID token carries no tenant or object id" };
  const tenant = str(env.MICROSOFT_TENANT_ID).toLowerCase();
  if (MULTI_TENANT.has(tenant)) {
    const edov = claims.xms_edov;
    if (edov !== true && edov !== "true" && edov !== 1 && edov !== "1") {
      return { ok: false, why: "the Microsoft ID token does not say its email domain is verified (xms_edov)" };
    }
  } else if (GUID.test(tenant) && tid !== tenant) {
    return { ok: false, why: "the Microsoft account belongs to another tenant" };
  }
  const email = emailOf(claims);
  if (!email) return { ok: false, why: "the Microsoft ID token carries no email address" };
  return { ok: true, email, subject: `microsoft:${tid}:${oid}` };
}

/** The verified identity behind an OAuth sign-in, or why there is none. */
export function verifiedProviderIdentity(
  provider: string | undefined,
  input: { account?: Claims | null; profile?: Claims },
  env: Record<string, string | undefined> = process.env,
): ProviderIdentity {
  if (provider === "google") return google(input.profile);
  if (provider === "microsoft") return microsoft(input.account, env);
  return { ok: false, why: `unknown provider ${provider ?? "(none)"}` };
}
