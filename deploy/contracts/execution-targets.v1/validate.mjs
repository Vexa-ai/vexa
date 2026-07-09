#!/usr/bin/env node
/**
 * gate:schema (L1) for execution-targets.v1 — validate the registry goldens (and, with `--file PATH`,
 * the deploy/execution-targets[.example].json files that live outside golden/) against
 * execution-targets.schema.json. The goldens are the spec (P8). Secrets are NEVER inline — the schema's
 * `secret_ref` pattern enforces a reference (`vexa-secrets:`/`env:`) only (P14).
 * Run: node validate.mjs [--check] [--file PATH]...
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "execution-targets.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validate = ajv.compile(schema);

const extra = [];
for (let i = 2; i < process.argv.length; i++) if (process.argv[i] === "--file") extra.push(process.argv[++i]);

const goldenDir = join(HERE, "golden");
const goldens = existsSync(goldenDir)
  ? readdirSync(goldenDir).filter((n) => n.endsWith(".json")).map((n) => join(goldenDir, n))
  : [];
const files = [...goldens, ...extra];

let failed = 0;
for (const f of files) {
  const label = relative(HERE, f);
  let data;
  try { data = JSON.parse(readFileSync(f, "utf8")); }
  catch (e) { console.error(`  ✗ ${label}: ${e.message}`); failed++; continue; }
  if (validate(data)) {
    // referential: default_target must resolve to a defined target.
    const names = new Set((data.targets || []).map((t) => t.name));
    if (data.default_target && !names.has(data.default_target)) {
      console.error(`  ✗ ${label}: default_target "${data.default_target}" is not a defined target`); failed++; continue;
    }
    console.log(`  ✓ ${label} ≡ execution-targets.v1 (${(data.targets || []).length} target(s), ${(data.resources || []).length} resource(s))`);
  } else { console.error(`  ✗ ${label}: ${ajv.errorsText(validate.errors)}`); failed++; }
}
console.log(failed ? `execution-targets.v1: ${failed} FAILED` : `execution-targets.v1: ${files.length} instance(s) conform`);
process.exit(failed ? 1 : 0);
