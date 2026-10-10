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
 *  - **Generic OIDC** (`oidc`, ADFS / Keycloak, `./oidcConfig.mjs`) — the claims come from the ID token
 *    the token endpoint returned, which openid-client has already verified against the issuer's keys,
 *    our client id and this sign-in's nonce. Its `iss` must also be the configured issuer, it must
 *    carry a `sub`, and the address is read from the configured claim (`VEXA_OIDC_EMAIL_CLAIM`,
 *    default `email`). The issuer is the operator's own directory, so its word on the address is
 *    taken, as with a pinned Microsoft tenant; with `VEXA_OIDC_REQUIRE_EMAIL_VERIFIED=1` the token
 *    must also say `email_verified: true`.
 *
 *  The stable subject (`google:<sub>`, `microsoft:<tid>:<oid>`, `oidc:<sha256(iss, sub)>`) is
 *  returned with the email, and the
 *  account is BOUND to it (`findOrCreateUserToken` → admin-api `PUT /internal/users/{id}/provider-subject`):
 *  the first sign-in through a provider records the subject, and a later one with another subject is
 *  refused — so inside a pinned tenant, an administrator who writes somebody's address into another
 *  user's `email` does not reach that account.
 */

import { createHash } from "node:crypto";
import { isWellFormedEmail } from "./emailAddress";
import { OIDC_PROVIDER_ID, oidcConfig } from "./oidcConfig.mjs";

export type ProviderIdentity =
  | { ok: true; email: string; subject: string }
  | { ok: false; why: string };

const MULTI_TENANT = new Set(["", "common", "organizations", "consumers"]);
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

function emailOf(claims: Claims, claim = "email"): string | null {
  const email = str(claims[claim]).toLowerCase();
  return isWellFormedEmail(email) ? email : null;
}

/** What to call the person, from an OIDC token's claims: the configured name claim, else
 *  `given_name family_name`, else nothing (the caller falls back to the address). */
export function oidcDisplayName(claims: Claims, nameClaim = "name"): string | null {
  const named = str(claims[nameClaim]);
  if (named) return named.slice(0, 200);
  const joined = [str(claims.given_name), str(claims.family_name)].filter(Boolean).join(" ");
  return joined ? joined.slice(0, 200) : null;
}

/** The binding subject for an OIDC identity. `sub` is only unique within its issuer, and an ADFS
 *  `sub` is a base64 string, so both are hashed into one fixed-alphabet value: the same person at the
 *  same issuer always gives the same subject, and a different issuer never does. */
export function oidcSubject(iss: string, sub: string): string {
  return `oidc:${createHash("sha256").update(`${iss}\n${sub}`).digest("hex")}`;
}

const stripSlash = (u: string) => u.replace(/\/+$/, "");

function oidc(account: Claims | null | undefined, env: Record<string, string | undefined>): ProviderIdentity {
  const cfg = oidcConfig(env);
  if (!cfg.enabled) return { ok: false, why: "generic OIDC sign-in is not configured" };
  const claims = jwtClaims(account?.id_token);
  if (!claims) return { ok: false, why: "no ID token from the OIDC provider" };
  const iss = str(claims.iss);
  if (!iss || stripSlash(iss) !== stripSlash(cfg.issuer)) {
    return { ok: false, why: "the ID token was issued by another issuer" };
  }
  const sub = str(claims.sub);
  if (!sub) return { ok: false, why: "the ID token carries no subject" };
  if (cfg.requireEmailVerified && claims.email_verified !== true && claims.email_verified !== "true") {
    return { ok: false, why: "the OIDC provider has not verified this email address" };
  }
  const email = emailOf(claims, cfg.emailClaim);
  if (!email) return { ok: false, why: `the ID token carries no email address in the ${cfg.emailClaim} claim` };
  return { ok: true, email, subject: oidcSubject(stripSlash(iss), sub) };
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
  if (provider === OIDC_PROVIDER_ID) return oidc(input.account, env);
  return { ok: false, why: `unknown provider ${provider ?? "(none)"}` };
}
