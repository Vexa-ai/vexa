#!/usr/bin/env node
/**
 * gate:schema for transcription-language.v1 — the goldens are the spec (P8). Filenames are
 * `<Shape>.<case>.json`:
 *   <Shape>.*  → conforms to #/$defs/<Shape> AND passes the membership rule below;
 *   Refused.*  → `{shape, value, reason}`: `value` is refused as `shape`, by the schema or the rule.
 * The membership rule (the one the schema cannot say): when `language` is a code and the list is
 * non-empty, the code is in the list. meeting-api (`bot_spawn/transcription_language.py`) and
 * identity's admin-api read these same goldens in their tests, so this is one of three readings.
 * And the contract holds together: every x-routes row names shapes that exist.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "transcription-language.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const shape = (name) => ajv.getSchema(`${schema.$id}#/$defs/${name}`);

let failed = 0, checked = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };

for (const r of schema["x-routes"] || []) {
  for (const k of ["request", "response"]) {
    if (!schema.$defs[r[k]]) bad(`route ${r.method} ${r.path}: ${k} shape ${r[k]} is not a $def`);
  }
}

/** The language and list a value states, whichever shape it is. */
function languageAndList(name, doc) {
  if (name === "DeploymentDefault") {
    const list = doc.DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES;
    return [doc.DEFAULT_TRANSCRIPTION_LANGUAGE || null, list ? list.split(",") : []];
  }
  return [doc.language, Array.isArray(doc.allowed_languages) ? doc.allowed_languages : []];
}

/** Why `doc` is refused as `name`, or [] when it is accepted. */
function refusals(name, doc) {
  const v = shape(name);
  if (!v) return [`no $def named ${name}`];
  if (!v(doc)) return [ajv.errorsText(v.errors)];
  const [language, list] = languageAndList(name, doc);
  const code = typeof language === "string" && /^[a-z]{2,3}$/.test(language) ? language : null;
  if (code && list.length && !list.includes(code)) return [`language ${code} is not one of ${list.join(",")}`];
  return [];
}

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json")).sort();
for (const f of files) {
  const name = f.split(".")[0];
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  checked++;
  if (name === "Refused") {
    if (!data.shape || !("value" in data) || !data.reason) { bad(`${f}: a Refused golden is {shape, value, reason}`); continue; }
    const why = refusals(data.shape, data.value);
    if (why.length) console.log(`  ✓ ${f} refused as ${data.shape} (${why[0]})`);
    else bad(`${f}: accepted as ${data.shape}, but it is meant to be refused (${data.reason})`);
    continue;
  }
  if (!schema.$defs[name]) { bad(`${f}: no $def named ${name}`); continue; }
  const why = refusals(name, data);
  if (why.length) bad(`${f} (${name}): ${why.join("; ")}`);
  else console.log(`  ✓ ${f} ≡ ${name}`);
}
console.log(failed ? `transcription-language.v1: ${failed} check(s) FAILED` : `transcription-language.v1: ${checked} goldens hold`);
process.exit(failed ? 1 : 0);
