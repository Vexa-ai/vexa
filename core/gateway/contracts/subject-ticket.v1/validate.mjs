#!/usr/bin/env node
/**
 * gate:schema for subject-ticket.v1 — the goldens are the spec (P8).
 *   vector-*  → a signing vector, validated against #/$defs/Vector, then RE-MADE here in a second
 *               language: the claims serialized (keys sorted, no whitespace) must be the token's
 *               payload, Ed25519 over "st1." + payload with the RFC 8032 TEST 1 key (derived below
 *               from its published seed, never read from a golden) must reproduce the token's
 *               signature byte for byte, the vector's public key must be that key's public half and
 *               verify it, and the claims must hold to the issuer policy restated below (each class
 *               one the issuer may grant, the lifetime within the (issuer, kind) cap).
 *   refused-* → a refusal vector, validated against #/$defs/Refusal, then checked here with the
 *               verifier's rules restated: the token must be refused for `reason` at `now`. A token
 *               refused for its signature or shape must NOT verify; one refused for a claim
 *               (expired, not_yet_valid, lifetime_too_long, issuer_not_allowed) verifies and is
 *               refused by the claim check that names the reason.
 * No golden may carry private-key material: a PEM private-key header anywhere in one fails.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "ticket.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const claimsOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Claims` });
const vectorOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Vector` });
const refusalOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Refusal` });

// The issuer policy (subject_ticket.ISSUER_CLASSES / MAX_LIFETIME), restated.
const DAY = 86400;
const ISSUER_CLASSES = {
  meetings: ["transcript.read", "chat.autonomous", "desk.write", "room.bills_to"],
  agent: ["routine.turn", "bot.dispatch"],
  mail: ["bot.dispatch", "transcript.read", "chat.autonomous", "desk.write"],
  gateway: ["desk.write", "routine.turn", "bot.dispatch"],
};
const MAX_LIFETIME = {
  "meetings/event": 3 * DAY, "agent/event": DAY, "agent/grant": 400 * DAY,
  "mail/event": 60 * DAY, "gateway/event": 600, "gateway/grant": 400 * DAY,
};
const MAX_SKEW_SEC = 60;
// The goldens' verifier holds a key for these issuers (all under TEST 1), and for no other.
const HELD = Object.keys(ISSUER_CLASSES);

const b64u = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
const B64U = /^[A-Za-z0-9_-]*$/;
const canon = (v) => Array.isArray(v) ? `[${v.map(canon).join(",")}]`
  : v && typeof v === "object" ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`
  : JSON.stringify(v);

// RFC 8032 section 7.1 TEST 1: the published Ed25519 test seed the vectors are made with.
const RFC8032_TEST1_SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";
const ED25519_PKCS8_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const TEST1_KEY = createPrivateKey({
  key: Buffer.concat([ED25519_PKCS8_PREFIX, Buffer.from(RFC8032_TEST1_SEED_HEX, "hex")]),
  format: "der",
  type: "pkcs8",
});
const TEST1_PUBLIC = createPublicKey(TEST1_KEY).export({ type: "spki", format: "pem" });
const PRIVATE_PEM = /-----BEGIN [A-Z ]*PRIVATE KEY-----/;

// The verifier (subject_ticket.read), restated: the reason a token is refused, or null.
function refusal(publicPem, token, now) {
  const key = createPublicKey(publicPem);
  if (key.asymmetricKeyType !== "ed25519") throw new Error("the verification key is not Ed25519");
  const parts = token.split(".");
  if (!token || parts.length !== 3 || parts[0] !== "st1") return "malformed";
  if (!B64U.test(parts[1]) || !B64U.test(parts[2])) return "malformed";
  const raw = unb64u(parts[1]), sig = unb64u(parts[2]);
  if (b64u(raw) !== parts[1] || b64u(sig) !== parts[2]) return "malformed";
  let claims;
  try { claims = JSON.parse(raw.toString("utf8")); } catch { return "malformed"; }
  if (!claims || typeof claims !== "object" || Array.isArray(claims) || claims.v !== "subject-ticket.v1") return "malformed";
  if (typeof claims.iss !== "string" || !HELD.includes(claims.iss)) return "unknown_issuer";
  if (sig.length !== 64 || !verify(null, Buffer.from(`${parts[0]}.${parts[1]}`), key, sig)) return "bad_signature";
  const { iss, kind, iat, exp, cls } = claims;
  if (!Number.isInteger(iat) || !Number.isInteger(exp) || exp <= iat) return "malformed";
  const cap = MAX_LIFETIME[`${iss}/${kind}`];
  if (cap === undefined) return "issuer_not_allowed";
  if (exp - iat > cap) return "lifetime_too_long";
  if (!Array.isArray(cls) || !cls.length || cls.some((c) => !ISSUER_CLASSES[iss].includes(c))) return "issuer_not_allowed";
  if (typeof claims.sub !== "string" || typeof claims.jti !== "string") return "malformed";
  if (!claims.sub.trim() && !String(claims.addr || "").trim()) return "malformed";
  if (iat > now + MAX_SKEW_SEC) return "not_yet_valid";
  if (now >= exp) return "expired";
  return null;
}

let failed = 0;
const files = readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const text = readFileSync(join(HERE, "golden", f), "utf8");
  if (PRIVATE_PEM.test(text)) { console.error(`  ✗ ${f}: carries private-key material; derive the key in code`); failed++; continue; }
  const data = JSON.parse(text);
  if (f.startsWith("vector-")) {
    if (!vectorOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(vectorOk.errors)}`); failed++; continue; }
    if (data.public_key !== TEST1_PUBLIC) { console.error(`  ✗ ${f}: public_key is not the RFC 8032 TEST 1 public key`); failed++; continue; }
    const [version, payload, sig] = data.token.split(".");
    if (b64u(Buffer.from(canon(data.claims))) !== payload) { console.error(`  ✗ ${f}: the token's payload is not the claims, keys sorted`); failed++; continue; }
    if (b64u(sign(null, Buffer.from(`${version}.${payload}`), TEST1_KEY)) !== sig) { console.error(`  ✗ ${f}: signature does not reproduce`); failed++; continue; }
    const why = refusal(data.public_key, data.token, data.now);
    if (why !== null) { console.error(`  ✗ ${f}: the verifier restated here refuses it (${why})`); failed++; continue; }
    const c = data.claims;
    if (c.iat !== data.now || c.exp !== data.now + data.ttl_sec) { console.error(`  ✗ ${f}: iat/exp disagree with now/ttl_sec`); failed++; continue; }
    if (!claimsOk(c)) { console.error(`  ✗ ${f}: ${ajv.errorsText(claimsOk.errors)}`); failed++; continue; }
    console.log(`  ✓ ${f} ≡ Vector (${c.iss} ${c.kind}: signature reproduced and verified, within the issuer policy)`);
  } else if (f.startsWith("refused-")) {
    if (!refusalOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(refusalOk.errors)}`); failed++; continue; }
    const why = refusal(data.public_key, data.token, data.now);
    if (why !== data.reason) { console.error(`  ✗ ${f}: refused here for ${why}, the golden says ${data.reason}`); failed++; continue; }
    console.log(`  ✓ ${f} ≡ Refusal (${data.reason})`);
  } else {
    console.error(`  ✗ ${f}: filename must start with vector- / refused-`); failed++;
  }
}
console.log(failed ? `subject-ticket.v1: ${failed} golden(s) FAILED` : `subject-ticket.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
