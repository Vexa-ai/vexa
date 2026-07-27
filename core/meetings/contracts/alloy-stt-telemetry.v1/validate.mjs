#!/usr/bin/env node
/**
 * ALLOY: gate:schema for alloy-stt-telemetry.v1.
 * A golden named `<Shape>.<case>.json` validates against `#/$defs/<Shape>`.
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(
  readFileSync(join(HERE, "alloy-stt-telemetry.schema.json"), "utf8"),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);

const goldenDir = join(HERE, "golden");
const files = readdirSync(goldenDir).filter((name) => name.endsWith(".json"));
let failed = 0;

for (const file of files) {
  const shape = file.split(".")[0];
  const validate = ajv.compile({
    $ref: `${schema.$id}#/$defs/${shape}`,
  });
  const value = JSON.parse(readFileSync(join(goldenDir, file), "utf8"));
  if (validate(value)) {
    console.log(`  ✓ ${file} ≡ ${shape}`);
  } else {
    console.error(`  ✗ ${file} (${shape}): ${ajv.errorsText(validate.errors)}`);
    failed++;
  }
}

console.log(
  failed
    ? `alloy-stt-telemetry.v1: ${failed} golden(s) FAILED`
    : `alloy-stt-telemetry.v1: ${files.length} goldens conform`,
);
process.exit(failed ? 1 : 0);
