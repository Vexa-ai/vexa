#!/usr/bin/env node
/**
 * Generate unit.v1's FAULT vocabulary — every `Fault.source` and, per source, every `kind` — for each
 * side that names one, FROM the schema.
 *
 * The worker, agent-api and the terminal cannot share a module, and a kind spelled on one side only
 * is a fault the other side renders as a raw identifier (or one an emitter sends that no contract
 * names). So no side spells them: each imports a file generated here, and `validate.mjs --check`
 * (gate:schema) fails when a generated file no longer matches the schema. Precedent: signin.v1.
 *
 * Two Python targets carry the same text: `llm/` imports nothing from product code
 * (core/agent/llm/README.md § Rules), so the model-provider side cannot import `shared/`'s copy.
 *
 * Run: node core/agent/contracts/unit.v1/gen-faults.mjs          (write)
 *      node core/agent/contracts/unit.v1/gen-faults.mjs --check  (exit 1 on drift)
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..", "..");
const SCHEMA = join(HERE, "unit.schema.json");

export const TARGETS = {
  "python-llm": join(ROOT, "core/agent/llm/fault_wire.py"),
  "python-shared": join(ROOT, "core/agent/shared/fault_wire.py"),
  typescript: join(ROOT, "clients/terminal/src/surfaces/faultWire.ts"),
};

/** `model-provider` → `ModelProvider`; `spawn_refused` → `SPAWN_REFUSED`. */
const pascal = (s) => s.split(/[-_]/).map((w) => w[0].toUpperCase() + w.slice(1)).join("");
const upper = (s) => s.replace(/-/g, "_").toUpperCase();

/** The `$def` holding a source's kind enum, by convention: `model-provider` → `ModelProviderFaultKind`. */
export const kindDef = (source) => `${pascal(source)}FaultKind`;

/** `{sources: [...], kinds: {source: [...]}}`, read from the schema, refusing a partial one. */
export function vocab(schema) {
  const sources = schema.$defs?.FaultSource?.enum;
  if (!Array.isArray(sources) || !sources.length) throw new Error("unit.v1: $defs.FaultSource.enum is missing");
  const kinds = {};
  for (const s of sources) {
    const values = schema.$defs?.[kindDef(s)]?.enum;
    if (!Array.isArray(values) || !values.length) throw new Error(`unit.v1: fault source "${s}" has no $defs.${kindDef(s)}.enum`);
    kinds[s] = values;
  }
  return { sources, kinds };
}

const HEADER = "GENERATED from core/agent/contracts/unit.v1/unit.schema.json by gen-faults.mjs — DO NOT EDIT.\n"
  + "Regenerate with: node core/agent/contracts/unit.v1/gen-faults.mjs";

const q = (s) => JSON.stringify(s);
const pyTuple = (xs) => `(${xs.map(q).join(", ")}${xs.length === 1 ? "," : ""})`;

export function render(schema = JSON.parse(readFileSync(SCHEMA, "utf8"))) {
  const { sources, kinds } = vocab(schema);
  const py = [
    `"""${HEADER}`,
    "",
    "The typed-fault vocabulary (unit.v1 ``Fault``): every ``source`` a fault may name — WHO failed — and, per",
    "source, every ``kind`` — HOW. One class per source holds its ``SOURCE``, one constant per kind and",
    "``KINDS``. Generated with the same text into core/agent/llm/fault_wire.py and",
    "core/agent/shared/fault_wire.py (llm/ imports nothing from product code), and into the terminal's",
    "clients/terminal/src/surfaces/faultWire.ts.",
    '"""',
    "from __future__ import annotations",
    "",
    "from typing import Dict, Tuple",
    "",
    `SOURCES: Tuple[str, ...] = ${pyTuple(sources)}`,
    "",
    ...sources.flatMap((s) => [
      "",
      `class ${pascal(s)}:`,
      `    """source ${q(s)} and its kinds."""`,
      "",
      `    SOURCE = ${q(s)}`,
      ...kinds[s].map((k) => `    ${upper(k)} = ${q(k)}`),
      `    KINDS: Tuple[str, ...] = ${pyTuple(kinds[s])}`,
      "",
    ]),
    "",
    "#: source → its closed kind vocabulary.",
    "KINDS: Dict[str, Tuple[str, ...]] = {",
    ...sources.map((s) => `    ${pascal(s)}.SOURCE: ${pascal(s)}.KINDS,`),
    "}",
    "",
  ].join("\n");
  const ts = [
    `/** ${HEADER.replace("\n", "\n *  ")}`,
    " *",
    " *  The typed-fault vocabulary (unit.v1 `Fault`): every `source` a fault may name — WHO failed — and, per",
    " *  source, every `kind` — HOW. Shared with the worker and agent-api through",
    " *  core/agent/llm/fault_wire.py and core/agent/shared/fault_wire.py, generated from the same schema. */",
    "",
    `export const FAULT_SOURCES = [${sources.map(q).join(", ")}] as const;`,
    "export type FaultSource = (typeof FAULT_SOURCES)[number];",
    "",
    "export const FAULT_KINDS = {",
    ...sources.map((s) => `  ${/^[a-z]+$/.test(s) ? s : q(s)}: [${kinds[s].map(q).join(", ")}],`),
    "} as const;",
    "export type FaultKind = (typeof FAULT_KINDS)[FaultSource][number];",
    "",
    "export const isFaultSource = (x: unknown): x is FaultSource =>",
    "  typeof x === \"string\" && (FAULT_SOURCES as readonly string[]).includes(x);",
    "",
    "/** Is `kind` in `source`'s vocabulary? A source this release does not know has none. */",
    "export const isFaultKind = (source: string, kind: unknown): kind is FaultKind =>",
    "  isFaultSource(source) && typeof kind === \"string\" && (FAULT_KINDS[source] as readonly string[]).includes(kind);",
    "",
  ].join("\n");
  return { "python-llm": py, "python-shared": py, typescript: ts };
}

/** The generated files that no longer match the schema (relative paths). */
export function drift() {
  const want = render();
  return Object.entries(TARGETS)
    .filter(([lang, path]) => !existsSync(path) || readFileSync(path, "utf8") !== want[lang])
    .map(([, path]) => relative(ROOT, path));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--check")) {
    const stale = drift();
    if (stale.length) {
      console.error(`unit.v1: generated fault vocabularies out of date: ${stale.join(", ")} — run node core/agent/contracts/unit.v1/gen-faults.mjs`);
      process.exit(1);
    }
    console.log("unit.v1: generated fault vocabularies match the schema");
  } else {
    const out = render();
    for (const [lang, path] of Object.entries(TARGETS)) writeFileSync(path, out[lang]);
    console.log(`unit.v1: wrote ${Object.values(TARGETS).map((p) => relative(ROOT, p)).join(", ")}`);
  }
}
