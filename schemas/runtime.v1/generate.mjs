#!/usr/bin/env node
/**
 * gate:schema (level 1) for runtime.v1 — validate every golden vector against runtime.schema.json.
 * The goldens are the spec (P8). Golden filenames start with the shape: spec- / status- / event-.
 * Run: node generate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "runtime.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const SHAPE = { spec: "WorkloadSpec", status: "WorkloadStatus", event: "RuntimeEvent" };
const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const shape = SHAPE[f.split("-")[0]];
  if (!shape) { console.error(`  ✗ ${f}: filename must start with spec- / status- / event-`); failed++; continue; }
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (validate(data)) console.log(`  ✓ ${f} ≡ ${shape}`);
  else { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; }
}
console.log(failed ? `runtime.v1: ${failed} golden(s) FAILED` : `runtime.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
