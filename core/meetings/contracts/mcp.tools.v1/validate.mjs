#!/usr/bin/env node
/**
 * gate:schema for mcp.tools.v1 — the goldens are the spec (P8), and every manifest in the repository
 * is held to it. Filenames are `<Shape>.<case>.json`:
 *   Manifest.* → conforms to #/$defs/Manifest and passes the cross-tool rules below.
 *   Refused.*  → its `manifest` is refused, by the schema or by a cross-tool rule.
 * Then every `mcp.tools.v1.json` (or `*.mcp.tools.v1.json`) under core/ is validated as a Manifest,
 * and across all of them no tool name is claimed twice and at most one entitlement hook exists.
 * The cross-tool rules restate `core/meetings/services/mcp/src/vexa_mcp/manifest.py`, which refuses
 * the same at boot:
 *   - `requires` names only the domain and identity, unless the manifest declares `composes: true`;
 *   - no argument, trimmed and lower-cased, is a #/$defs/CredentialArgument;
 *   - `alias_of` names another, non-alias tool on the same route with the same arguments, and two
 *     non-alias tools never share a route;
 *   - with a `forward`, every route path starts with the upstream prefix.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..", "..", "..");
const schema = JSON.parse(readFileSync(join(HERE, "mcp.tools.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const shape = (name) => ajv.getSchema(`${schema.$id}#/$defs/${name}`);
const CREDENTIALS = new Set(schema.$defs.CredentialArgument.enum);
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** The cross-tool refusals for one manifest, or [] when it holds. */
function crossTool(doc) {
  const out = [], tools = doc.tools || [], byName = new Map(tools.map((t) => [t.name, t])), first = new Map();
  const allowed = doc.composes === true ? null : new Set([doc.domain, "identity"]);
  for (const t of tools) {
    if (allowed && !t.requires.every((d) => allowed.has(d))) out.push(`${t.name}: requires ${t.requires} reaches past ${[...allowed]} without composes`);
    for (const a of t.arguments || []) if (CREDENTIALS.has(a.trim().toLowerCase())) out.push(`${t.name}: credential argument ${JSON.stringify(a)}`);
    if (doc.forward && t.route && !t.route.path.startsWith(doc.forward.upstream_prefix)) out.push(`${t.name}: ${t.route.path} is outside the forward ${doc.forward.upstream_prefix}…`);
    if (t.alias_of !== undefined) {
      const target = byName.get(t.alias_of);
      if (t.alias_of === t.name || !target || !same(target.route, t.route) || !same(target.arguments || [], t.arguments || []) || target.alias_of !== undefined)
        out.push(`${t.name}: alias_of must name another, non-alias tool on the same route with the same arguments`);
      continue;
    }
    if (!t.route) continue;
    const key = `${t.route.method} ${t.route.path}`;
    if (first.has(key)) out.push(`${first.get(key)} and ${t.name} share ${key} without alias_of`);
    else first.set(key, t.name);
  }
  return out;
}
const refusals = (doc) => {
  const v = shape("Manifest");
  return v(doc) ? crossTool(doc) : [ajv.errorsText(v.errors)];
};

let failed = 0, checked = 0;
const bad = (msg) => { console.error(`  ✗ ${msg}`); failed++; };
for (const f of readdirSync(join(HERE, "golden")).filter((n) => n.endsWith(".json"))) {
  const name = f.split(".")[0];
  if (!schema.$defs[name]) { bad(`${f}: no $def named ${name}`); continue; }
  const data = JSON.parse(readFileSync(join(HERE, "golden", f), "utf8"));
  const v = shape(name);
  if (!v(data)) { bad(`${f} (${name}): ${ajv.errorsText(v.errors)}`); continue; }
  if (name === "Manifest") { const r = crossTool(data); if (r.length) { bad(`${f}: ${r.join("; ")}`); continue; } }
  if (name === "Refused" && refusals(data.manifest).length === 0) { bad(`${f}: accepted, but it must be refused (${data.why})`); continue; }
  checked++;
}

const manifests = [];
(function walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith(".") || ["node_modules", "dist", "__pycache__"].includes(e.name)) continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name === "mcp.tools.v1.json" || e.name.endsWith(".mcp.tools.v1.json")) manifests.push(p);
  }
})(join(REPO, "core"));
const owner = new Map(), hooks = [];
for (const p of manifests.sort()) {
  const doc = JSON.parse(readFileSync(p, "utf8"));
  const where = relative(REPO, p);
  const r = refusals(doc);
  if (r.length) { bad(`${where}: ${r.join("; ")}`); continue; }
  if (doc.entitlement) hooks.push(where);
  for (const t of doc.tools || []) {
    if (owner.has(t.name)) bad(`tool ${t.name} is claimed by both ${owner.get(t.name)} and ${where}`);
    owner.set(t.name, where);
  }
}
if (hooks.length > 1) bad(`two entitlement hooks: ${hooks.join(", ")}`);
if (!manifests.length) bad("no mcp.tools.v1.json found under core/");
console.log(failed ? `mcp.tools.v1: ${failed} FAILED` : `mcp.tools.v1: ${checked} goldens conform; ${manifests.length} manifests (${owner.size} tools) hold`);
process.exit(failed ? 1 : 0);
