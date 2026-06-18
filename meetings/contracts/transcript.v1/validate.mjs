#!/usr/bin/env node
/**
 * gate:schema for transcript.v1 — validate every golden against transcript.schema.json.
 * Convention: a golden filename is `<Shape>.<case>.json`; the part before the first dot is the
 * `$def` it must conform to (e.g. `TranscriptSegment.glow.json` → #/$defs/TranscriptSegment).
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "transcript.schema.json"), "utf8"));
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
console.log(failed ? `transcript.v1: ${failed} golden(s) FAILED` : `transcript.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
