#!/usr/bin/env node
/**
 * gate:schema for gateway-identity.v1 — the goldens are the spec (P8).
 *   claims-*  → a signed payload, validated against #/$defs/Claims
 *   vector-*  → a signing vector, validated against #/$defs/Vector, then RE-SIGNED here in a second
 *               language: the token's payload must decode to Claims, and HMAC-SHA256 over
 *               "v1." + payload with the vector's secret must reproduce the token's signature.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "identity.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const claimsOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Claims` });
const vectorOk = ajv.compile({ $ref: `${schema.$id}#/$defs/Vector` });

const b64u = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

let failed = 0;
const files = readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const data = JSON.parse(readFileSync(join(HERE, "golden", f), "utf8"));
  if (f.startsWith("claims-")) {
    if (!claimsOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(claimsOk.errors)}`); failed++; continue; }
    console.log(`  ✓ ${f} ≡ Claims`);
  } else if (f.startsWith("vector-")) {
    if (!vectorOk(data)) { console.error(`  ✗ ${f}: ${ajv.errorsText(vectorOk.errors)}`); failed++; continue; }
    const [version, payload, sig] = data.token.split(".");
    const expect = b64u(createHmac("sha256", data.secret).update(`${version}.${payload}`).digest());
    if (expect !== sig) { console.error(`  ✗ ${f}: signature does not reproduce`); failed++; continue; }
    const claims = JSON.parse(unb64u(payload).toString("utf8"));
    if (!claimsOk(claims)) { console.error(`  ✗ ${f}: payload is not Claims: ${ajv.errorsText(claimsOk.errors)}`); failed++; continue; }
    if (claims.iat !== data.now || claims.exp !== data.now + data.ttl_sec || claims.sub !== data.claims.sub) {
      console.error(`  ✗ ${f}: payload iat/exp/sub disagree with the vector`); failed++; continue;
    }
    console.log(`  ✓ ${f} ≡ Vector (signature reproduced)`);
  } else {
    console.error(`  ✗ ${f}: filename must start with claims- / vector-`); failed++;
  }
}
console.log(failed ? `gateway-identity.v1: ${failed} golden(s) FAILED` : `gateway-identity.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
