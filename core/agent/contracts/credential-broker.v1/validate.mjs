#!/usr/bin/env node
/**
 * gate:schema for credential-broker.v1 — three checks, all offline:
 *   1. every golden conforms to its `$def` (filename `<Shape>.<case>.json`, prefix = the $def);
 *   2. every route in `x-routes` names request/response shapes that exist in `$defs`;
 *   3. every SignedAssertionVector re-derives: base64url(claims JSON) === encoded,
 *      HMAC-SHA256(key, encoded) === signature, header === encoded.signature, and
 *      sha256(request_body) === claims.body. The Python and TypeScript bindings are tested
 *      against the same vectors, so this is the third, independent derivation of one wire.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHash, createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "credential-broker.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

let failed = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const shape = f.split(".")[0];
  if (!schema.$defs[shape]) { bad(`${f}: no $def named ${shape}`); continue; }
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (validate(data)) console.log(`  ✓ ${f} ≡ ${shape}`);
  else bad(`${f} (${shape}): ${ajv.errorsText(validate.errors)}`);
}

for (const r of schema["x-routes"] || []) {
  for (const k of ["request", "response"]) {
    if (r[k] && !schema.$defs[r[k]]) bad(`route ${r.method} ${r.path}: ${k} shape ${r[k]} is not a $def`);
  }
  for (const role of r.roles || []) {
    if (!schema.$defs.Role.enum.includes(role)) bad(`route ${r.method} ${r.path}: unknown role ${role}`);
  }
}

const ORDER = ["role", "actor", "session", "at", "nonce", "method", "path", "body"];
for (const f of files.filter((n) => n.startsWith("SignedAssertionVector."))) {
  const v = JSON.parse(readFileSync(join(dir, f), "utf8"));
  const ordered = Object.fromEntries(ORDER.map((k) => [k, v.claims[k]]));
  const encoded = Buffer.from(JSON.stringify(ordered)).toString("base64url");
  const signature = createHmac("sha256", Buffer.from(v.key, "utf8")).update(encoded).digest("hex");
  const digest = createHash("sha256").update(Buffer.from(v.request_body, "utf8")).digest("hex");
  if (encoded !== v.encoded) bad(`${f}: encoded does not re-derive from claims`);
  else if (signature !== v.signature) bad(`${f}: signature does not re-derive from key + encoded`);
  else if (v.header !== `${v.encoded}.${v.signature}`) bad(`${f}: header is not encoded.signature`);
  else if (digest !== v.claims.body) bad(`${f}: claims.body is not sha256(request_body)`);
  else console.log(`  ✓ ${f} re-derives (encoded · HMAC · header · body digest)`);
}

console.log(failed
  ? `credential-broker.v1: ${failed} check(s) FAILED`
  : `credential-broker.v1: ${files.length} goldens conform; ${(schema["x-routes"] || []).length} routes resolve`);
process.exit(failed ? 1 : 0);
