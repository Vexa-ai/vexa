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
 * catalog.json (the languages each model can transcribe) is held to itself: unique codes, every code
 * an entry names is one of `languages`, every `match` compiles. Its reading — model id → list — is
 * pinned by the ModelLookup.* goldens, and a LanguageCatalog golden lists exactly what its model
 * reads to (all of `languages` when `source` is assumed). meeting-api's
 * `bot_spawn/language_catalog.py` reads the same file and the same goldens.
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
    if (k === "request" && r.method === "GET" && !(k in r)) continue;
    if (!schema.$defs[r[k]]) bad(`route ${r.method} ${r.path}: ${k} shape ${r[k]} is not a $def`);
  }
}

const catalog = JSON.parse(readFileSync(join(HERE, "catalog.json"), "utf8"));
const allCodes = catalog.languages.map((l) => l.code);
if (new Set(allCodes).size !== allCodes.length) bad("catalog.json: a code is listed twice");
for (const l of catalog.languages) {
  if (!/^[a-z]{2,3}$/.test(l.code) || !l.name) bad(`catalog.json: ${JSON.stringify(l)} is not {code, name}`);
}
for (const m of catalog.models) {
  try { new RegExp(m.match); } catch { bad(`catalog.json: match ${m.match} does not compile`); }
  const named = m.only || m.except;
  if (!Array.isArray(named) || (m.only && m.except)) bad(`catalog.json: ${m.match} names exactly one of only/except`);
  for (const c of named || []) if (!allCodes.includes(c)) bad(`catalog.json: ${m.match} names ${c}, not in languages`);
}

/** The codes catalog.json lists for a model id, or null when no entry matches it. */
function lookup(model) {
  const id = model.toLowerCase().split("/").pop();
  const entry = catalog.models.find((m) => new RegExp(m.match).test(id));
  if (!entry) return null;
  return entry.only ? allCodes.filter((c) => entry.only.includes(c)) : allCodes.filter((c) => !entry.except.includes(c));
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
  if (name === "ModelLookup") {
    const codes = lookup(doc.model);
    if ((codes ? codes.length : null) !== doc.count) return [`${doc.model} reads to ${codes ? codes.length : "no"} codes, not ${doc.count}`];
    for (const c of doc.has || []) if (!codes || !codes.includes(c)) return [`${doc.model} does not list ${c}`];
    for (const c of doc.lacks || []) if (codes && codes.includes(c)) return [`${doc.model} lists ${c}`];
    return [];
  }
  if (name === "LanguageCatalog") {
    if (doc.source !== "assumed" && !doc.model) return [`source ${doc.source} names a model`];
    const want = doc.source === "assumed" ? allCodes : lookup(doc.model);
    if (!want) return [`model ${doc.model} is not in catalog.json, so its source is assumed`];
    const got = doc.languages.map((l) => l.code);
    if (got.join(",") !== want.join(",")) return [`languages ${got.join(",")} are not the catalog list ${want.join(",")}`];
    const names = Object.fromEntries(catalog.languages.map((l) => [l.code, l.name]));
    const misnamed = doc.languages.find((l) => names[l.code] !== l.name);
    if (misnamed) return [`${misnamed.code} is named ${misnamed.name}, not ${names[misnamed.code]}`];
    return refusals("LanguageSetting", doc.default);
  }
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
