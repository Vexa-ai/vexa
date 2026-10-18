#!/usr/bin/env node
/**
 * gate:schema for event.v2 — golden `<Shape>.<case>.json` validates against #/$defs/<Shape>.
 * A golden named `<Shape>.reject-<case>.json` must FAIL to validate: the refusals (a content field,
 * an unenumerated lifecycle key, a caller-claimed audience) are as much the contract as the shape.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "event.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const shape = f.split(".")[0];
  const reject = f.split(".")[1].startsWith("reject-");
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  const ok = validate(data);
  if (ok !== reject) console.log(`  ✓ ${f} ${reject ? "refused by" : "≡"} ${shape}`);
  else {
    console.error(`  ✗ ${f} (${shape}): ${reject ? "accepted, must be refused" : ajv.errorsText(validate.errors)}`);
    failed++;
  }
}
console.log(failed ? `event.v2: ${failed} golden(s) FAILED` : `event.v2: ${files.length} goldens hold`);
process.exit(failed ? 1 : 0);
