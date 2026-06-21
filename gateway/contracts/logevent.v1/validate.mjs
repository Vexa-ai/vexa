#!/usr/bin/env node
/**
 * gate:schema for logevent.v1 — validate every golden vector against logevent.schema.json.
 * The goldens are the spec (P8). Filename-prefix → shape (same pattern as runtime.v1):
 * every line is a LogEvent; the prefix names the AUDIENCE/INTENT class the golden exercises:
 *   user-*   → a user-facing event (audience=user)
 *   system-* → a system/debug event (audience=system)
 *   error-*  → an error-level system event
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "logevent.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

// Every golden prefix validates against the single LogEvent shape; the prefix documents intent.
const SHAPE = { user: "LogEvent", system: "LogEvent", error: "LogEvent" };
// Extra per-prefix assertions beyond the schema, so the goldens prove the audience split.
const EXTRA = {
  user: (d) => (d.audience === "user" ? null : "user-* golden must carry audience=user"),
  system: (d) => (d.audience === "system" ? null : "system-* golden must carry audience=system"),
  error: (d) =>
    d.audience === "system"
      ? d.level === "error" || d.level === "critical"
        ? null
        : "error-* golden must be level error|critical"
      : "error-* golden must carry audience=system",
};

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const prefix = f.split("-")[0];
  const shape = SHAPE[prefix];
  if (!shape) { console.error(`  ✗ ${f}: filename must start with user- / system- / error-`); failed++; continue; }
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (!validate(data)) { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  const extra = EXTRA[prefix](data);
  if (extra) { console.error(`  ✗ ${f}: ${extra}`); failed++; continue; }
  console.log(`  ✓ ${f} ≡ ${shape} (audience=${data.audience})`);
}
console.log(failed ? `logevent.v1: ${failed} golden(s) FAILED` : `logevent.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
