#!/usr/bin/env node
/**
 * gate:schema for routes.v1 — the goldens are the spec (P8), and every manifest in the repository is
 * held to it. Filenames are `<Shape>.<case>.json`:
 *   Manifest.* → conforms to #/$defs/Manifest and passes the cross-row rules below.
 *   Refused.*  → its `manifest` is refused, by the schema or by a cross-row rule.
 * Then every `routes.v1.json` under core/ is validated as a Manifest, and the rows of all of them
 * together hold one owner per (method, path), as the gateway assembles them.
 * The cross-row rules restate `gateway/routes_manifest.py`, which refuses the same at boot:
 *   - one manifest declares (method, path) once;
 *   - with a forward, a path is the edge prefix followed by literal segments and whole `{name}`
 *     parameters, no name twice, or the prefix's `{path:path}` catch-all;
 *   - a `verbs` row is strictly under the edge prefix and declared once, as agent-api's
 *     `control_plane/route_policy.py` refuses at boot;
 *   - a row's `upstream` names each `{name}` parameter once, and only parameters its own path
 *     matches, so the edge can fill the hop from what it matched.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..", "..", "..");
const schema = JSON.parse(readFileSync(join(HERE, "routes.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const shape = (name) => ajv.getSchema(`${schema.$id}#/$defs/${name}`);

const CATCH_ALL = "{path:path}";
const PARAM = /^\{([A-Za-z_][A-Za-z0-9_]*)\}$/;
function forwardable(tail) {
  if (!tail) return false;
  if (tail === CATCH_ALL) return true;
  const names = [];
  for (const segment of tail.split("/")) {
    if (!segment.includes("{") && !segment.includes("}")) continue;
    const m = PARAM.exec(segment);
    if (!m || names.includes(m[1])) return false;
    names.push(m[1]);
  }
  return true;
}
/** The whole-segment `{name}` parameters of a template, in order. */
const params = (path) => path.split("/").map((s) => PARAM.exec(s)).filter(Boolean).map((m) => m[1]);
/** The cross-row refusals for one manifest, or [] when it holds. */
function crossRow(doc) {
  const out = [], seen = new Set();
  for (const r of doc.routes || []) {
    const key = `${r.method} ${r.path}`;
    if (seen.has(key)) out.push(`${key} is declared twice`);
    seen.add(key);
    if (doc.forward) {
      const p = doc.forward.edge_prefix;
      if (!r.path.startsWith(p) || !forwardable(r.path.slice(p.length))) out.push(`${key} is outside the forward ${p}…`);
    }
    if (r.upstream !== undefined) {
      const mine = new Set(params(r.path)), theirs = params(r.upstream);
      if (new Set(theirs).size !== theirs.length) out.push(`${key}: upstream ${r.upstream} names a parameter twice`);
      const extra = theirs.filter((n) => !mine.has(n));
      if (extra.length) out.push(`${key}: upstream ${r.upstream} names ${extra.join(", ")}, which the row does not match`);
    }
  }
  const verbs = new Set();
  for (const v of doc.verbs || []) {
    const key = `${v.method} ${v.path}`;
    if (verbs.has(key)) out.push(`verbs: ${key} is declared twice`);
    verbs.add(key);
    const p = doc.forward?.edge_prefix;
    if (p && (!v.path.startsWith(p) || v.path === p)) out.push(`verbs: ${key} is outside the forward ${p}…`);
  }
  return out;
}
const refusals = (doc) => {
  const v = shape("Manifest");
  return v(doc) ? crossRow(doc) : [ajv.errorsText(v.errors)];
};

let failed = 0, checked = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };
for (const f of readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"))) {
  const name = f.split(".")[0];
  if (!schema.$defs[name]) { bad(`${f}: no $def named ${name}`); continue; }
  const data = JSON.parse(readFileSync(join(HERE, "golden", f), "utf8"));
  const v = shape(name);
  if (!v(data)) { bad(`${f} (${name}): ${ajv.errorsText(v.errors)}`); continue; }
  if (name === "Manifest") { const r = crossRow(data); if (r.length) { bad(`${f}: ${r.join("; ")}`); continue; } }
  if (name === "Refused" && refusals(data.manifest).length === 0) { bad(`${f}: accepted, but it must be refused (${data.why})`); continue; }
  checked++;
}

// Every manifest in the repository, and their union as the edge assembles it.
const manifests = [];
(function walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith(".") || ["node_modules", "dist", "__pycache__"].includes(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name === "routes.v1.json") manifests.push(p);
  }
})(join(REPO, "core"));
const owner = new Map();
for (const p of manifests.sort()) {
  const doc = JSON.parse(readFileSync(p, "utf8"));
  const where = relative(REPO, p);
  const r = refusals(doc);
  if (r.length) { bad(`${where}: ${r.join("; ")}`); continue; }
  for (const row of doc.routes) {
    const key = `${row.method} ${row.path}`;
    if (owner.has(key)) bad(`${key} is declared by both ${owner.get(key)} and ${where}`);
    owner.set(key, where);
  }
}
if (!manifests.length) bad("no routes.v1.json found under core/");
console.log(failed ? `routes.v1: ${failed} FAILED` : `routes.v1: ${checked} goldens conform; ${manifests.length} manifests (${owner.size} rows) hold`);
process.exit(failed ? 1 : 0);
