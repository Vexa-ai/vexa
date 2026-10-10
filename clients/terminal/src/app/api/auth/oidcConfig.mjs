/** The generic OpenID Connect sign-in door (ADFS, Keycloak, any OIDC issuer) — its configuration,
 *  and which sign-in methods this instance offers at all.
 *
 *  Plain JavaScript on purpose, like `authSecret.mjs`: `server.mjs` imports it to refuse to start on a
 *  half-configured provider, before Next is loaded, and the TypeScript routes import the same file, so
 *  both read one rule.
 *
 *  The provider exists only when `VEXA_OIDC_ISSUER` is set. Once it is, everything it needs must be
 *  usable or the terminal does not start (P18): a client id and secret, an `https://` issuer, and — when
 *  `VEXA_OIDC_CA_FILE` is set — a readable file holding at least one PEM certificate. A sign-in door that
 *  silently is not there would send an operator hunting for a button; a refusal at boot names the key.
 *
 *  `VEXA_SIGNIN_METHODS` narrows the doors: a comma-separated subset of `google`, `microsoft`, `oidc`
 *  and `email`. Unset, every configured door is offered (the behaviour before this key existed). A
 *  method left out is not hidden in the page only — its provider is not registered and, for `email`,
 *  the link routes refuse — so a door the operator closed cannot be reached by typing its URL.
 */
import { readFileSync } from "node:fs";

/** The NextAuth provider id. Fixed, because it is part of the redirect URI the identity provider
 *  registers: `<public URL>/api/auth/callback/oidc`. */
export const OIDC_PROVIDER_ID = "oidc";

/** Every sign-in method the terminal knows. */
export const SIGNIN_METHODS = Object.freeze(["google", "microsoft", "oidc", "email"]);

const DEFAULT_SCOPES = "openid email profile";
const DEFAULT_DISPLAY_NAME = "Single sign-on";
const CLAIM_NAME = /^[A-Za-z0-9_.:/-]{1,128}$/;
const PEM_CERT = /-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g;

const str = (v) => (typeof v === "string" ? v.trim() : "");
const truthy = (v) => ["1", "true", "yes", "on"].includes(str(v).toLowerCase());

/** Which sign-in methods this instance offers, as a Set. Unknown entries make it invalid (see
 *  `signinMethodsProblem`); here they are simply dropped. */
/** @param {Env} [env] @returns {Set<string>} */
export function signinMethods(env = process.env) {
  const raw = str(env.VEXA_SIGNIN_METHODS);
  if (!raw) return new Set(SIGNIN_METHODS);
  return new Set(raw.split(",").map((m) => m.trim().toLowerCase()).filter((m) => SIGNIN_METHODS.includes(m)));
}

/** True when `method` is offered on this instance. */
/** @param {string} method @param {Env} [env] @returns {boolean} */
export function signinMethodEnabled(method, env = process.env) {
  return signinMethods(env).has(method);
}

/** Why `VEXA_SIGNIN_METHODS` is unusable, or null. */
/** @param {Env} [env] @returns {string | null} */
export function signinMethodsProblem(env = process.env) {
  const raw = str(env.VEXA_SIGNIN_METHODS);
  if (!raw) return null;
  const entries = raw.split(",").map((m) => m.trim().toLowerCase()).filter(Boolean);
  const unknown = entries.filter((m) => !SIGNIN_METHODS.includes(m));
  if (unknown.length) return `VEXA_SIGNIN_METHODS names unknown method(s) ${unknown.join(", ")} (known: ${SIGNIN_METHODS.join(", ")})`;
  if (!entries.length) return "VEXA_SIGNIN_METHODS is set but names no method";
  return null;
}

/** The PEM certificates in `text`, each as its own string. */
/** @param {unknown} text @returns {string[]} */
export function pemCertificates(text) {
  return typeof text === "string" ? text.match(PEM_CERT) || [] : [];
}

/**
 * @typedef {Record<string, string | undefined>} Env
 * @typedef {{ enabled: true, issuer: string, wellKnown: string, clientId: string, clientSecret: string,
 *   scopes: string, displayName: string, emailClaim: string, nameClaim: string,
 *   requireEmailVerified: boolean, ca: string[] | null }} OidcEnabled
 * @typedef {{ enabled: false, problem?: string }} OidcDisabled
 */

/** The OIDC provider's configuration, read from `env`:
 *  `{ enabled: false }` when no issuer is set, `{ enabled: true, ... }` when it is usable, and
 *  `{ enabled: false, problem }` when an issuer is set but something it needs is not.
 *  @param {Env} [env]
 *  @param {(path: string, enc: "utf8") => string} [readFile]
 *  @returns {OidcEnabled | OidcDisabled} */
export function oidcConfig(env = process.env, readFile = readFileSync) {
  const issuer = str(env.VEXA_OIDC_ISSUER);
  if (!issuer) return /** @type {OidcDisabled} */ ({ enabled: false });
  /** @param {string} problem @returns {OidcDisabled} */
  const fail = (problem) => ({ enabled: false, problem });

  let url;
  try {
    url = new URL(issuer);
  } catch {
    return fail("VEXA_OIDC_ISSUER is not a URL");
  }
  if (url.protocol !== "https:") return fail("VEXA_OIDC_ISSUER must be an https:// URL");
  if (url.username || url.password) return fail("VEXA_OIDC_ISSUER must not carry credentials");

  const clientId = str(env.VEXA_OIDC_CLIENT_ID);
  const clientSecret = str(env.VEXA_OIDC_CLIENT_SECRET);
  if (!clientId) return fail("VEXA_OIDC_CLIENT_ID is not set");
  if (!clientSecret) return fail("VEXA_OIDC_CLIENT_SECRET is not set");

  const scopes = str(env.VEXA_OIDC_SCOPES) || DEFAULT_SCOPES;
  if (!scopes.split(/\s+/).includes("openid")) return fail("VEXA_OIDC_SCOPES must include openid");

  const emailClaim = str(env.VEXA_OIDC_EMAIL_CLAIM) || "email";
  const nameClaim = str(env.VEXA_OIDC_NAME_CLAIM) || "name";
  for (const [key, v] of [["VEXA_OIDC_EMAIL_CLAIM", emailClaim], ["VEXA_OIDC_NAME_CLAIM", nameClaim]]) {
    if (!CLAIM_NAME.test(v)) return fail(`${key} is not a claim name`);
  }

  let ca = null;
  const caFile = str(env.VEXA_OIDC_CA_FILE);
  if (caFile) {
    let text;
    try {
      text = readFile(caFile, "utf8");
    } catch (e) {
      return fail(`VEXA_OIDC_CA_FILE ${caFile} cannot be read (${e.code || e.message})`);
    }
    ca = pemCertificates(text);
    if (!ca.length) return fail(`VEXA_OIDC_CA_FILE ${caFile} holds no PEM certificate`);
  }

  // The discovery document lives under the issuer; openid-client reads it and checks that the
  // document's own `issuer` is exactly this value, so a trailing slash must match the IdP's.
  const wellKnown = `${issuer.replace(/\/+$/, "")}/.well-known/openid-configuration`;

  return /** @type {OidcEnabled} */ ({
    enabled: true,
    issuer,
    wellKnown,
    clientId,
    clientSecret,
    scopes,
    displayName: str(env.VEXA_OIDC_DISPLAY_NAME) || DEFAULT_DISPLAY_NAME,
    emailClaim,
    nameClaim,
    requireEmailVerified: truthy(env.VEXA_OIDC_REQUIRE_EMAIL_VERIFIED),
    ca,
  });
}

/** Why the terminal must refuse to start over its sign-in configuration, or null. */
/** @param {Env} [env] @param {(path: string, enc: "utf8") => string} [readFile] @returns {string | null} */
export function signinConfigStartupError(env = process.env, readFile = readFileSync) {
  const methods = signinMethodsProblem(env);
  if (methods) return methods;
  const oidc = oidcConfig(env, readFile);
  if (oidc.problem) return `generic OIDC sign-in is misconfigured: ${oidc.problem}`;
  if (str(env.VEXA_SIGNIN_METHODS) && signinMethods(env).has("oidc") && !oidc.enabled) {
    return "VEXA_SIGNIN_METHODS offers oidc but VEXA_OIDC_ISSUER is not set";
  }
  return null;
}
