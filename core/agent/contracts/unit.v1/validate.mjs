#!/usr/bin/env node
/** gate:schema for unit.v1 — golden `<Shape>.<case>.json` validates against #/$defs/<Shape>.
 *  An InputVector is also re-derived here: key = hex HMAC-SHA256(secret, "vexa-unit-input.v1:" + unit
 *  id), sig = hex HMAC-SHA256(key bytes, turn) — shared/unit_input.py, restated in a second language. */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "unit.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
const UNIT_INPUT_LABEL = "vexa-unit-input.v1:";
const hex = (key, msg) => createHmac("sha256", key).update(Buffer.from(msg, "utf8")).digest("hex");
for (const f of files) {
  const shape = f.split(".")[0];
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (!validate(data)) { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  if (shape === "InputVector") {
    const key = hex(Buffer.from(data.secret, "utf8"), UNIT_INPUT_LABEL + data.unit_id);
    if (key !== data.key || hex(Buffer.from(key, "hex"), data.turn) !== data.sig) {
      console.error(`  ✗ ${f}: re-deriving the unit key or the signature does not reproduce the vector`); failed++; continue;
    }
  }
  console.log(`  ✓ ${f} ≡ ${shape}`);
}
console.log(failed ? `unit.v1: ${failed} golden(s) FAILED` : `unit.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
