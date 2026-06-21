#!/usr/bin/env node
/**
 * gate:schema for flagged-issue.v1 — validate every golden against flagged-issue.schema.json.
 * Convention (same as transcript.v1): a golden filename is `<Shape>.<case>.json`; the part before
 * the first dot is the `$def` it must conform to (e.g. `FlaggedIssue.misattr.json` → #/$defs/FlaggedIssue).
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "flagged-issue.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const shape = f.split(".")[0];
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (validate(data)) console.log(`  ✓ ${f} ≡ ${shape}`);
  else { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; }
}
console.log(failed ? `flagged-issue.v1: ${failed} golden(s) FAILED` : `flagged-issue.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
