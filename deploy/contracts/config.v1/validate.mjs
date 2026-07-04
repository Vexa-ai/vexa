#!/usr/bin/env node
/**
 * gate:schema (L1) for config.v1 — validate the declaration goldens (and, with `--file PATH`, the
 * LIVE per-service declarations that live next to each adopted service's code) against
 * config.schema.json. The goldens are the spec (P8). Beyond the schema, the referential rules the
 * schema cannot express are checked here: every `capability`-classed key names a declared
 * capability, every declared capability has at least one member key, probe url/auth/path keys are
 * declared keys, and key/surface_only names are unique.
 * Run: node validate.mjs [--check] [--file PATH]...
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "config.schema.json"), "utf8"));
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
  if (!validate(data)) {
    console.error(`  ✗ ${label}: ${ajv.errorsText(validate.errors)}`); failed++; continue;
  }
  // referential rules the schema cannot express
  const errs = [];
  const caps = data.capabilities || {};
  const seen = new Set();
  const members = Object.fromEntries(Object.keys(caps).map((c) => [c, 0]));
  for (const k of data.keys || []) {
    if (seen.has(k.key)) errs.push(`duplicate key declaration: ${k.key}`);
    seen.add(k.key);
    if (k.class === "capability") {
      if (!(k.capability in caps)) errs.push(`key ${k.key} names undeclared capability "${k.capability}"`);
      else members[k.capability]++;
    }
  }
  for (const [c, n] of Object.entries(members)) if (!n) errs.push(`capability "${c}" has no member keys`);
  for (const [c, cap] of Object.entries(caps)) {
    const probe = cap.probe || {};
    for (const ref of [probe.http?.url_key, probe.http?.auth_key, probe.file?.path_key]) {
      if (ref && !seen.has(ref)) errs.push(`capability "${c}" probe references undeclared key ${ref}`);
    }
  }
  const soSeen = new Set();
  for (const s of data.surface_only || []) {
    if (seen.has(s.key)) errs.push(`surface_only key ${s.key} is also a declared key (pick one)`);
    if (soSeen.has(s.key)) errs.push(`duplicate surface_only key: ${s.key}`);
    soSeen.add(s.key);
  }
  if (errs.length) { for (const e of errs) console.error(`  ✗ ${label}: ${e}`); failed++; continue; }
  console.log(`  ✓ ${label} ≡ ConfigDeclaration (${(data.keys || []).length} keys, ${Object.keys(caps).length} capabilities)`);
}
console.log(failed ? `config.v1: ${failed} file(s) FAILED` : `config.v1: ${files.length} declaration(s) conform`);
process.exit(failed ? 1 : 0);
