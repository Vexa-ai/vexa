#!/usr/bin/env node
/**
 * gate:schema for flows.v1 — validate the LIVE carrier census, then every golden, against
 * flows.schema.json.
 *
 * The census (`carriers.json`) is checked first and by name, unlike every sibling contract, because
 * here the contract's document is not an example of a wire shape — it IS the registry the gate and
 * the flows suite read. A validator that only walked `golden/` would leave the one file anybody
 * actually consumes unchecked.
 *
 * Golden convention, unchanged: a filename is `<Shape>.<case>.json`; the part before the first dot
 * is the `$def` it must conform to (e.g. `Carrier.onboarding-completed.json` → #/$defs/Carrier).
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "flows.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const check = (label, shape, data) => {
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  if (validate(data)) { console.log(`  ✓ ${label} ≡ ${shape}`); return 0; }
  console.error(`  ✗ ${label} (${shape}): ${ajv.errorsText(validate.errors)}`);
  return 1;
};

let failed = 0;
let checked = 0;

const census = join(HERE, "carriers.json");
if (existsSync(census)) {
  const doc = JSON.parse(readFileSync(census, "utf8"));
  failed += check("carriers.json", "CarrierRegistry", doc);
  checked++;
  // ONE PRODUCING DOMAIN PER CARRIER — the census's first promise, and not something JSON Schema
  // can state: `uniqueItems` compares whole objects, so two entries for the same event with
  // different owners are two distinct items and pass. That is precisely the shape of the defect
  // (a second producer added by somebody who did not read the first), so it is checked here.
  const seen = new Map();
  for (const c of doc.carriers || []) {
    if (seen.has(c.event)) {
      console.error(`  ✗ carriers.json: ${c.event} is registered twice (${seen.get(c.event)} and ${c.owner}) — a carrier has exactly one producing domain`);
      failed++;
    }
    seen.set(c.event, c.owner);
  }
}

const dir = join(HERE, "golden");
if (existsSync(dir)) {
  for (const f of readdirSync(dir).filter((n) => n.endsWith(".json"))) {
    failed += check(f, f.split(".")[0], JSON.parse(readFileSync(join(dir, f), "utf8")));
    checked++;
  }
}

console.log(failed ? `flows.v1: ${failed} document(s) FAILED` : `flows.v1: ${checked} document(s) conform`);
process.exit(failed ? 1 : 0);
