#!/usr/bin/env node
/**
 * gate:schema for session-profile.v1 — the goldens are the spec (P8). Filenames are `<Shape>.<case>.json`:
 *   WritebackBody.*   → conforms to #/$defs/WritebackBody AND passes SessionProfile's rules below;
 *   WritebackResult.* → conforms to #/$defs/WritebackResult;
 *   Refused.*         → its `body` is refused, by the WritebackBody shape or by a profile rule;
 *   PathVectors.*     → every vector's path is answered `inProfile` by the matcher below.
 * And the contract holds together:
 *   - SessionProfile's const is a Profile, its `leveldbFile` compiles, and its limits are the ones
 *     WritebackBody states (files.maxItems = maxFiles, data.maxLength = base64 of maxFileBytes);
 *   - every x-routes row names request/response shapes that exist.
 * The profile rules restate meeting-api's `session_profile/profile.py` and @vexa/remote-browser's
 * `isSessionProfilePath`; both runtimes are tested against these same goldens, so this is the third,
 * independent reading of one rule.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "session-profile.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const shape = (name) => ajv.getSchema(`${schema.$id}#/$defs/${name}`);

let failed = 0, checked = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };

// ── the profile itself ──────────────────────────────────────────────────────────────────────────
const PROFILE = schema.$defs.SessionProfile.const;
if (!shape("Profile")(PROFILE)) bad(`SessionProfile const is not a Profile: ${ajv.errorsText(shape("Profile").errors)}`);
let LEVELDB_FILE = /$^/;
try { LEVELDB_FILE = new RegExp(PROFILE.leveldbFile); } catch (e) { bad(`SessionProfile leveldbFile does not compile: ${e.message}`); }
const body = schema.$defs.WritebackBody.properties.files;
if (body.maxItems !== PROFILE.maxFiles) bad(`WritebackBody files.maxItems ${body.maxItems} is not SessionProfile.maxFiles ${PROFILE.maxFiles}`);
const encodedMax = Math.ceil(PROFILE.maxFileBytes / 3) * 4;
const dataMax = schema.$defs.WritebackEntry.properties.data.maxLength;
if (dataMax !== encodedMax) bad(`WritebackEntry data.maxLength ${dataMax} is not the base64 length of maxFileBytes (${encodedMax})`);
for (const r of schema["x-routes"] || []) {
  for (const k of ["request", "response"]) {
    if (!schema.$defs[r[k]]) bad(`route ${r.method} ${r.path}: ${k} shape ${r[k]} is not a $def`);
  }
}

/** True when `path` is in SessionProfile: a ProfilePath that is listed, or a LevelDB dir's data file. */
function inProfile(path) {
  if (!shape("ProfilePath")(path)) return false;
  if (PROFILE.files.includes(path)) return true;
  const parts = path.split("/");
  return PROFILE.leveldbDirs.includes(parts.slice(0, -1).join("/")) && LEVELDB_FILE.test(parts[parts.length - 1]);
}

/** Why a write-back body is refused, or [] when it is accepted. */
function refusals(doc) {
  const v = shape("WritebackBody");
  if (!v(doc)) return [ajv.errorsText(v.errors)];
  const out = [], seen = new Set();
  let total = 0;
  doc.files.forEach((f, i) => {
    if (!inProfile(f.path)) out.push(`entry ${i} (${JSON.stringify(f.path)}) is not part of the session profile`);
    if (seen.has(f.path)) out.push(`entry ${i} (${JSON.stringify(f.path)}) is named twice`);
    seen.add(f.path);
    const size = Buffer.from(f.data, "base64").length;
    if (size > PROFILE.maxFileBytes) out.push(`entry ${i} is larger than ${PROFILE.maxFileBytes} bytes`);
    total += size;
  });
  if (total > PROFILE.maxTotalBytes) out.push(`the files exceed ${PROFILE.maxTotalBytes} bytes together`);
  return out;
}

// ── the goldens ─────────────────────────────────────────────────────────────────────────────────
const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json")).sort();
let vectors = 0;
for (const f of files) {
  const name = f.split(".")[0];
  if (!schema.$defs[name]) { bad(`${f}: no $def named ${name}`); continue; }
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  const v = shape(name);
  if (!v(data)) { bad(`${f} (${name}): ${ajv.errorsText(v.errors)}`); continue; }
  if (name === "WritebackBody") {
    const r = refusals(data);
    if (r.length) { bad(`${f}: ${r.join("; ")}`); continue; }
  }
  if (name === "Refused" && refusals(data.body).length === 0) { bad(`${f}: accepted, but it must be refused (${data.why})`); continue; }
  if (name === "PathVectors") {
    const wrong = data.vectors.filter((x) => inProfile(x.path) !== data.inProfile);
    if (wrong.length) { bad(`${f}: ${wrong.map((x) => JSON.stringify(x.path)).join(", ")} not answered inProfile=${data.inProfile}`); continue; }
    vectors += data.vectors.length;
  }
  checked++;
}
if (!files.some((f) => f.startsWith("PathVectors."))) bad("no PathVectors golden: the two runtime matchers would share nothing");

console.log(failed
  ? `session-profile.v1: ${failed} check(s) FAILED`
  : `session-profile.v1: ${checked} goldens conform (${vectors} path vectors); the profile, its limits and ${(schema["x-routes"] || []).length} route hold`);
process.exit(failed ? 1 : 0);
