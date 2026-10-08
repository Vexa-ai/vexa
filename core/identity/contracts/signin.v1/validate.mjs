#!/usr/bin/env node
/**
 * gate:schema for signin.v1 — every golden conforms to its `$def`, every known-bad body is refused,
 * and the reason vocabularies generated for admin-api and the terminal still match the schema.
 * Convention: a golden filename is `<Shape>.<case>.json`; the part before the first dot is the `$def`.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { drift } from "./gen.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "signin.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);
const shape = (name) => ajv.compile({ $ref: `${schema.$id}#/$defs/${name}` });

let failed = 0;
const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
for (const f of files) {
  const name = f.split(".")[0];
  const validate = shape(name);
  if (validate(JSON.parse(readFileSync(join(dir, f), "utf8")))) console.log(`  ✓ ${f} ≡ ${name}`);
  else { console.error(`  ✗ ${f} (${name}): ${ajv.errorsText(validate.errors)}`); failed++; }
}

// One golden per reason: a reason added to the schema without a golden is a reason nobody pinned.
for (const [def, golden] of [["AdmittedReason", "SigninAdmissionResponse"], ["RefusedReason", "SigninAdmissionResponse"],
                             ["ClaimReason", "AdminClaimResponse"]]) {
  for (const why of schema.$defs[def].enum) {
    if (!files.includes(`${golden}.${why}.json`)) { console.error(`  ✗ no golden ${golden}.${why}.json for ${def} "${why}"`); failed++; }
  }
}

// Bodies the wire must refuse — the shapes a caller fails closed on.
const MUST_FAIL = [
  ["SigninAdmissionResponse", { admitted: true, why: "not-allowed" }, "admitted with a refusal reason"],
  ["SigninAdmissionResponse", { admitted: false, why: "admin" }, "refused with an admission reason"],
  ["SigninAdmissionResponse", { admitted: true, why: "unclaimed-instance" }, "a reason the schema does not know"],
  ["SigninAdmissionResponse", { admitted: "true", why: "admin" }, "admitted that is not a literal true"],
  ["SigninAdmissionRequest", { email: "a@b.co", extra: 1 }, "an unknown request field"],
  ["AdminClaimResponse", { claimed: true, admin_exists: true }, "a claim answer with no reason"],
  ["AdminClaimRequest", { claim_code: "ABCD" }, "a claim with no user"],
];
for (const [name, body, why] of MUST_FAIL) {
  if (shape(name)(body)) { console.error(`  ✗ ${name} accepted ${why}: ${JSON.stringify(body)}`); failed++; }
  else console.log(`  ✓ ${name} refuses ${why}`);
}

const stale = drift();
for (const p of stale) { console.error(`  ✗ ${p} is out of date — run node core/identity/contracts/signin.v1/gen.mjs`); failed++; }
if (!stale.length) console.log("  ✓ generated reason vocabularies match the schema");

console.log(failed ? `signin.v1: ${failed} check(s) FAILED` : `signin.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
