#!/usr/bin/env node
/**
 * gate:schema for gateway-identity.v1 — the goldens are the spec (P8).
 *   claims-*  → a signed payload, validated against #/$defs/Claims
 *   vector-*  → a signing vector, validated against #/$defs/Vector, then RE-SIGNED here in a second
 *               language: Ed25519 over "v1." + payload with the RFC 8032 TEST 1 key (derived below
 *               from its published seed, never read from a golden) must reproduce the token's
 *               signature byte for byte, the vector's public key must be that key's public half and
 *               must verify it, and the payload must decode to Claims.
 *   refused-* → a refusal vector, validated against #/$defs/Refusal, then checked here: the token
 *               must NOT verify under the vector's public key (or must be refused before a
 *               signature is checked, for a malformed one or an over-long lifetime).
 *   headers-* → the x-user-* headers a service rebuilds from a claims-* golden (#/$defs/Headers),
 *               re-derived here from the mapping restated in Node.
 *   reentry-* → an MCP re-entry match vector (#/$defs/Reentry): the gateway's rule, restated here,
 *               must admit exactly the vectors marked admitted.
 * No golden may carry private-key material: a PEM private-key header anywhere in one fails.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "identity.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const claimsOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Claims` });
const vectorOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Vector` });
const refusalOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Refusal` });
const reentryOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Reentry` });
const headersOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Headers` });

// The claims -> x-user-* mapping (identity_token.headers_from_claims), restated. webhook_events is
// Python's json.dumps of a flat object: ", " and ": " separators.
const CLAIM_HEADERS = { sub: "x-user-id", email: "x-user-email", scopes: "x-user-scopes", limits: "x-user-limits",
  workspaces: "x-user-workspaces", webhook_url: "x-user-webhook-url", webhook_secret: "x-user-webhook-secret",
  webhook_events: "x-user-webhook-events" };
const pyDumps = (v) => v && typeof v === "object" && !Array.isArray(v)
  ? `{${Object.entries(v).map(([k, x]) => `${JSON.stringify(k)}: ${pyDumps(x)}`).join(", ")}}`
  : Array.isArray(v) ? `[${v.map(pyDumps).join(", ")}]` : JSON.stringify(v);
function headersFromClaims(c) {
  const out = {};
  for (const [claim, header] of Object.entries(CLAIM_HEADERS)) {
    const v = c[claim];
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    out[header] = claim === "scopes" || claim === "workspaces" ? v.join(",")
      : claim === "webhook_events" ? (typeof v === "string" ? v : pyDumps(v)) : String(v);
  }
  const d = c.delegation;
  if (d && typeof d === "object") {
    if (d.regime) out["x-user-regime"] = String(d.regime);
    if (d.workspaces === "*") out["x-user-delegation-workspaces"] = "*";
    else if (Array.isArray(d.workspaces)) out["x-user-delegation-workspaces"] = d.workspaces.join(",");
    if (d.target) out["x-user-delegation-target"] = String(d.target);
  }
  return out;
}

// The re-entry match rule, restated: the delegation the gateway signs for a validate answer
// (identity_token.claims_from_validation), then same person AND an equal delegation.
const canon = (v) => Array.isArray(v) ? `[${v.map(canon).join(",")}]`
  : v && typeof v === "object" ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`
  : JSON.stringify(v);
function signedDelegation(validation) {
  const d = validation.delegation;
  if (!d || typeof d !== "object" || Array.isArray(d)) return undefined;
  const out = { regime: String(d.regime || ""), workspaces: d.workspaces === "*" ? "*" : (d.workspaces || []).map(String) };
  if (d.target) out.target = String(d.target);
  return out;
}
function reentryAdmitted(validation, signed) {
  const want = signedDelegation(validation);
  return want !== undefined && signed.sub === String(validation.user_id)
    && signed.delegation !== undefined && canon(signed.delegation) === canon(want);
}

const b64u = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
const B64U = /^[A-Za-z0-9_-]+$/;

// RFC 8032 section 7.1 TEST 1: the published Ed25519 test seed (its "SECRET KEY") the signing vectors
// are made with. Public test data, and refused at boot by every service that loads an identity key.
const RFC8032_TEST1_SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";
// PKCS#8 wraps a raw Ed25519 seed behind this fixed 16-byte DER prefix (RFC 8410).
const ED25519_PKCS8_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const TEST1_KEY = createPrivateKey({
  key: Buffer.concat([ED25519_PKCS8_PREFIX, Buffer.from(RFC8032_TEST1_SEED_HEX, "hex")]),
  format: "der",
  type: "pkcs8",
});
const PRIVATE_PEM = /-----BEGIN [A-Z ]*PRIVATE KEY-----/;

// The verifier's rules, restated in a second language: v1 only, canonical base64url, exactly a
// 64-byte Ed25519 signature, the signature checked before the payload is read.
function verifies(publicPem, token) {
  const key = createPublicKey(publicPem);
  if (key.asymmetricKeyType !== "ed25519") throw new Error("the verification key is not Ed25519");
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;
  if (!B64U.test(parts[1]) || !B64U.test(parts[2])) return false;
  const sig = unb64u(parts[2]);
  if (b64u(sig) !== parts[2] || sig.length !== 64) return false;
  return verify(null, Buffer.from(`${parts[0]}.${parts[1]}`), key, sig);
}

let failed = 0;
const files = readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const text = readFileSync(join(HERE, "golden", f), "utf8");
  if (PRIVATE_PEM.test(text)) { console.error(`  ✗ ${f}: carries private-key material; derive the key in code`); failed++; continue; }
  const data = JSON.parse(text);
  if (f.startsWith("claims-")) {
    if (!claimsOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(claimsOk.errors)}`); failed++; continue; }
    console.log(`  ✓ ${f} ≡ Claims`);
  } else if (f.startsWith("vector-")) {
    if (!vectorOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(vectorOk.errors)}`); failed++; continue; }
    if (createPublicKey(TEST1_KEY).export({ type: "spki", format: "pem" }) !== data.public_key) {
      console.error(`  ✗ ${f}: public_key is not the RFC 8032 TEST 1 public key`); failed++; continue;
    }
    const [version, payload, sig] = data.token.split(".");
    if (b64u(sign(null, Buffer.from(`${version}.${payload}`), TEST1_KEY)) !== sig) {
      console.error(`  ✗ ${f}: signature does not reproduce`); failed++; continue;
    }
    if (!verifies(data.public_key, data.token)) { console.error(`  ✗ ${f}: public_key does not verify the token`); failed++; continue; }
    const claims = JSON.parse(unb64u(payload).toString("utf8"));
    if (!claimsOk(claims)) { console.error(`  ✗ ${f}: payload is not Claims: ${ajv.errorsText(claimsOk.errors)}`); failed++; continue; }
    if (claims.iat !== data.now || claims.exp !== data.now + data.ttl_sec || claims.sub !== data.claims.sub) {
      console.error(`  ✗ ${f}: payload iat/exp/sub disagree with the vector`); failed++; continue;
    }
    console.log(`  ✓ ${f} ≡ Vector (Ed25519 signature reproduced and verified)`);
  } else if (f.startsWith("refused-")) {
    if (!refusalOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(refusalOk.errors)}`); failed++; continue; }
    const ok = verifies(data.public_key, data.token);
    // A correctly signed token refused for its claims (lifetime) verifies here and is refused by
    // the claim checks the Python verifier applies after the signature; anything else must not.
    if (ok !== (data.reason === "lifetime_too_long" || data.reason === "expired" || data.reason === "not_yet_valid")) {
      console.error(`  ✗ ${f}: signature check disagrees with reason ${data.reason}`); failed++; continue;
    }
    console.log(`  ✓ ${f} ≡ Refusal (${data.reason})`);
  } else if (f.startsWith("headers-")) {
    if (!headersOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(headersOk.errors)}`); failed++; continue; }
    const claims = JSON.parse(readFileSync(join(HERE, "golden", data.claims_golden), "utf8"));
    if (canon(headersFromClaims(claims)) !== canon(data.headers)) {
      console.error(`  ✗ ${f}: the mapping restated here gives other headers for ${data.claims_golden}`); failed++; continue;
    }
    console.log(`  ✓ ${f} ≡ Headers (${Object.keys(data.headers).length} from ${data.claims_golden})`);
  } else if (f.startsWith("reentry-")) {
    if (!reentryOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(reentryOk.errors)}`); failed++; continue; }
    if (reentryAdmitted(data.validation, data.signed) !== data.admitted) {
      console.error(`  ✗ ${f}: the match rule says ${!data.admitted}, the vector says ${data.admitted}`); failed++; continue;
    }
    console.log(`  ✓ ${f} ≡ Reentry (${data.admitted ? "admitted" : "not re-entry"})`);
  } else {
    console.error(`  ✗ ${f}: filename must start with claims- / vector- / refused- / reentry- / headers-`); failed++;
  }
}
console.log(failed ? `gateway-identity.v1: ${failed} golden(s) FAILED` : `gateway-identity.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
