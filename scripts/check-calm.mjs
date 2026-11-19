#!/usr/bin/env node
/**
 * The CALM checker behind gate:calm — the architecture model (architecture.calm.json, FINOS CALM 1.2)
 * is valid CALM, conforms to the meeting-intelligence pattern, and every control it names resolves.
 *
 * Two halves, and the first runs before the second:
 *
 *   1. `checkCalmControls(root)` — offline. Every `requirement-url` and `config-url` under a
 *      `controls` block (nodes, relationships, flows, the document itself) must be a repository path
 *      to a JSON Schema that exists, parses and carries an `$id`. A URL with a scheme is refused:
 *      calm-cli fetches remote documents only from hosts the caller allow-lists, so a remote
 *      requirement reads green on one machine and red on the next, and calm-cli silently skips a
 *      requirement its loader reports as unimplemented. A control that cannot be resolved here is
 *      a control nobody checked.
 *   2. `runCalmCli(root)` — the pinned calm-cli validates the model against the CALM meta-schema and
 *      every control's requirement schema (`validate -a`), then against the pattern
 *      (`validate -p -a`). Both runs are needed: with `-p` calm-cli validates against the pattern
 *      alone, not against the meta-schema. `judgeCalmOutput` fails on any error and on any
 *      control-requirement output whatever its severity; other warnings are reported, not fatal.
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, normalize, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";

export const CALM_CLI_VERSION = "1.65.0";
export const CALM_SPEC = "https://calm.finos.org/release/1.2/meta/calm.json";
export const ARCH_FILE = "architecture.calm.json";
export const PATTERN_FILE = "calm/patterns/meeting-intelligence.pattern.json";

// Every requirement detail under a `controls` block, wherever it sits, with the JSON pointer that
// names it in calm-cli's own terms (/nodes/<id>/controls/<control>/requirements/<i>).
export function controlRequirements(doc) {
  const out = [];
  const walk = (o, path) => {
    if (Array.isArray(o)) { o.forEach((v, i) => walk(v, `${path}/${(v && v["unique-id"]) || i}`)); return; }
    if (!o || typeof o !== "object") return;
    for (const [k, v] of Object.entries(o)) {
      if (k === "controls" && v && typeof v === "object" && !Array.isArray(v)) {
        for (const [cid, c] of Object.entries(v)) {
          (c?.requirements || []).forEach((r, i) => out.push({ path: `${path}/controls/${cid}/requirements/${i}`, detail: r || {} }));
        }
      } else walk(v, `${path}/${k}`);
    }
  };
  walk(doc, "");
  return out;
}

function resolveRef(root, ref, what) {
  if (typeof ref !== "string" || !ref) return `${what} is missing`;
  if (/^[a-z][a-z0-9+.-]*:/i.test(ref)) {
    return `${what} '${ref}' is a URL — ship the schema in calm/controls/ and reference it by repository path (calm-cli fetches remote documents only from allow-listed hosts, so a URL resolves on one machine and not the next)`;
  }
  if (ref.startsWith("#")) return `${what} '${ref}' is a JSON pointer; the model references its controls by repository path`;
  const abs = resolve(root, ref);
  if (!(abs + sep).startsWith(resolve(root) + sep) || normalize(ref).startsWith("..")) return `${what} '${ref}' leaves the repository`;
  if (!existsSync(abs) || !statSync(abs).isFile()) return `${what} '${ref}' does not exist`;
  let doc;
  try { doc = JSON.parse(readFileSync(abs, "utf8")); } catch (e) { return `${what} '${ref}' is not valid JSON — ${e.message}`; }
  if (!doc || typeof doc !== "object" || !doc.$id) return `${what} '${ref}' has no $id (calm-cli will not load a schema without one)`;
  return null;
}

export function checkCalmControls(root, archFile = ARCH_FILE) {
  const errs = [];
  const file = join(root, archFile);
  if (!existsSync(file)) return { errs: [`${archFile} not found`], requirements: 0, schemas: [] };
  let doc;
  try { doc = JSON.parse(readFileSync(file, "utf8")); } catch (e) { return { errs: [`${archFile} is not valid JSON — ${e.message}`], requirements: 0, schemas: [] }; }
  if (doc.$schema !== CALM_SPEC) errs.push(`${archFile}: $schema is '${doc.$schema}', expected ${CALM_SPEC}`);
  const reqs = controlRequirements(doc);
  const schemas = new Set();
  for (const { path, detail } of reqs) {
    const e = resolveRef(root, detail["requirement-url"], "requirement-url");
    if (e) errs.push(`${path}: ${e}`); else schemas.add(detail["requirement-url"]);
    if ("config-url" in detail) {
      const ce = resolveRef(root, detail["config-url"], "config-url");
      if (ce) errs.push(`${path}: ${ce}`);
    } else if (!detail.config || typeof detail.config !== "object") {
      errs.push(`${path}: carries neither config nor config-url`);
    }
  }
  return { errs, requirements: reqs.length, schemas: [...schemas].sort() };
}

// calm-cli's JSON report → the failures that matter. Any error fails; a control-requirement output
// fails at any severity; an unparseable report or a non-zero exit with no error in it fails too.
export function judgeCalmOutput(label, status, stdout) {
  let r;
  try { r = JSON.parse(stdout); } catch { return { errs: [`${label}: calm-cli produced no JSON report (exit ${status})`], warnings: [] }; }
  const all = [...(r.jsonSchemaValidationOutputs || []), ...(r.spectralSchemaValidationOutputs || [])];
  const errs = all
    .filter((o) => o.severity === "error" || o.code === "control-requirement-validation")
    .map((o) => `${label}: ${o.severity} ${o.code} ${o.path} — ${String(o.message).split("\n")[0]}`);
  if (r.hasErrors && !errs.length) errs.push(`${label}: calm-cli reports errors it did not itemise`);
  if (status !== 0 && !errs.length) errs.push(`${label}: calm-cli exited ${status}`);
  const warnings = all.filter((o) => o.severity === "warning" && o.code !== "control-requirement-validation")
    .map((o) => `${o.code} ${o.path}`);
  return { errs, warnings };
}

export function runCalmCli(root) {
  const cli = ["-y", `@finos/calm-cli@${CALM_CLI_VERSION}`, "validate", "-f", "json"];
  const runs = [["model", [...cli, "-a", ARCH_FILE]], ["pattern", [...cli, "-p", PATTERN_FILE, "-a", ARCH_FILE]]];
  const errs = [], warnings = [];
  for (const [label, args] of runs) {
    const r = spawnSync("npx", args, { cwd: root, encoding: "utf8", maxBuffer: 64 << 20 });
    const j = judgeCalmOutput(label, r.status, r.stdout || "");
    errs.push(...j.errs); warnings.push(...j.warnings.map((w) => `${label}: ${w}`));
  }
  return { errs, warnings };
}
