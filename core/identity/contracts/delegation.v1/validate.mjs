#!/usr/bin/env node
/**
 * gate:schema for delegation.v1 — the goldens are the spec (P8). Filenames are `<Shape>.<case>.json`;
 * the prefix is the `$def` each must conform to. Then, in a second language:
 *   Vector.*  → re-mint here: canonical JSON (keys sorted at every depth, no whitespace) for the
 *               header and the claims built from the vector's inputs, HMAC-SHA256 with its secret,
 *               unpadded base64url. The result must equal `token` byte for byte and decode to
 *               `header` and `claims`.
 *   Refusal.* → verify here with the rules the schema states, in its order; the token must be
 *               refused with exactly `reason`.
 * The Python side is `delegation.py` in this folder, vendored byte for byte into agent-api and
 * admin-api (gate:fact-parity, fact `delegation-token`); the goldens were minted by it.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { createHmac, timingSafeEqual } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "delegation.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);

const PREFIX = "vxd_";
const AUDIENCE = "vexa-mcp";
const b64u = (buf) => Buffer.from(buf).toString("base64url");
const unb64u = (s) => Buffer.from(s, "base64url");
// Python's json.dumps(sort_keys=True, separators=(",", ":")): sorted at every depth, ASCII-escaped.
const canon = (v) => {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (v && typeof v === "object") return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`;
  return JSON.stringify(v).replace(/[\u007f-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
};
const hmac = (secret, body) => createHmac("sha256", Buffer.from(secret, "utf8")).update(body).digest();

function mint(v) {
  const workspaces = v.workspaces === "*" ? "*" : [...new Set(v.workspaces.map(String))].sort();
  const claims = { sub: v.subject, aud: AUDIENCE, scope: { regime: v.regime, workspaces },
                   iat: v.now, exp: v.now + v.ttl_sec, jti: v.jti };
  if ((v.target || "").trim()) claims.target = v.target.trim();
  const body = `${b64u(canon({ alg: "HS256", typ: "vxdlg" }))}.${b64u(canon(claims))}`;
  return `${PREFIX}${body}.${b64u(hmac(v.secret, body))}`;
}

// The verifier, restated from the schema's description: the signature before any claim is read.
function refusal(secret, token, now, revoked) {
  if (!token.startsWith(PREFIX)) return "not_delegated";
  const parts = token.slice(PREFIX.length).split(".");
  if (parts.length !== 3) return "malformed";
  const expect = hmac(secret, `${parts[0]}.${parts[1]}`);
  const got = unb64u(parts[2]);
  if (got.length !== expect.length || !timingSafeEqual(got, expect)) return "bad_signature";
  let claims;
  try { claims = JSON.parse(unb64u(parts[1]).toString("utf8")); } catch { return "malformed"; }
  if (!claims || typeof claims !== "object" || Array.isArray(claims)) return "malformed";
  if (claims.aud !== AUDIENCE) return "bad_audience";
  if (!claims.sub) return "malformed";
  if (!Number.isInteger(claims.exp) || now >= claims.exp) return "expired";
  if ((revoked || []).includes(claims.jti)) return "revoked";
  return null;
}

let failed = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };
const files = readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const shape = f.split(".")[0];
  if (!schema.$defs[shape]) { bad(`${f}: no $def named ${shape}`); continue; }
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(HERE, "golden", f), "utf8"));
  if (!validate(data)) { bad(`${f} (${shape}): ${ajv.errorsText(validate.errors)}`); continue; }
  if (shape === "Vector") {
    const token = mint(data);
    const [h, c] = data.token.slice(PREFIX.length).split(".");
    if (token !== data.token) bad(`${f}: re-minting the inputs does not reproduce the token`);
    else if (canon(JSON.parse(unb64u(h))) !== canon(data.header)) bad(`${f}: the token's header is not the vector's header`);
    else if (canon(JSON.parse(unb64u(c))) !== canon(data.claims)) bad(`${f}: the token's claims are not the vector's claims`);
    else if (refusal(data.secret, data.token, data.now, []) !== null) bad(`${f}: the minted token does not verify at now`);
    else console.log(`  ✓ ${f} re-mints (canonical JSON · HMAC-SHA256 · base64url) and verifies`);
    continue;
  }
  if (shape === "Refusal") {
    const reason = refusal(data.secret, data.token, data.now, data.revoked);
    if (reason !== data.reason) bad(`${f}: refused with ${reason}, the vector says ${data.reason}`);
    else console.log(`  ✓ ${f} refused: ${reason}`);
    continue;
  }
  console.log(`  ✓ ${f} ≡ ${shape}`);
}
console.log(failed ? `delegation.v1: ${failed} check(s) FAILED` : `delegation.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
