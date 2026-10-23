#!/usr/bin/env node
/**
 * Generate the signin.v1 reason vocabularies for both sides of the wire, FROM the schema.
 *
 * The terminal (TypeScript) and admin-api (Python) cannot share a module, and a reason spelled on one
 * side only is a sign-in the other side refuses without saying why. So neither side spells them: both
 * import a file generated here, and `validate.mjs --check` (gate:schema) fails when a generated file
 * no longer matches the schema.
 *
 * Run: node core/identity/contracts/signin.v1/gen.mjs          (write)
 *      node core/identity/contracts/signin.v1/gen.mjs --check  (exit 1 on drift)
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..", "..");
const SCHEMA = join(HERE, "signin.schema.json");

export const TARGETS = {
  python: join(ROOT, "core/identity/services/admin-api/src/admin_api/app/signin_wire.py"),
  typescript: join(ROOT, "clients/terminal/src/app/api/auth/signinWire.ts"),
};

const VOCABS = [
  ["ADMITTED_REASONS", "AdmittedReason"],
  ["REFUSED_REASONS", "RefusedReason"],
  ["CLAIM_REASONS", "ClaimReason"],
];

function vocab(schema) {
  const out = {};
  for (const [, def] of VOCABS) {
    const values = schema.$defs?.[def]?.enum;
    if (!Array.isArray(values) || !values.length) throw new Error(`signin.v1: $defs.${def}.enum is missing`);
    out[def] = values;
  }
  return out;
}

const HEADER = "GENERATED from core/identity/contracts/signin.v1/signin.schema.json by gen.mjs — DO NOT EDIT.\n"
  + "Regenerate with: node core/identity/contracts/signin.v1/gen.mjs";

export function render(schema = JSON.parse(readFileSync(SCHEMA, "utf8"))) {
  const v = vocab(schema);
  const codeMax = schema.$defs.ClaimCode.maxLength;
  const emailMax = schema.$defs.SigninAdmissionRequest.properties.email.maxLength;
  const py = [
    `"""${HEADER}`,
    "",
    "The reason vocabularies of the sign-in admission wire (signin.v1), shared with the terminal's",
    "clients/terminal/src/app/api/auth/signinWire.ts, which is generated from the same schema.",
    '"""',
    "from __future__ import annotations",
    "",
    "from typing import Literal, Tuple",
    "",
    ...VOCABS.flatMap(([constName, def]) => [
      `${constName}: Tuple[str, ...] = (${v[def].map((x) => JSON.stringify(x)).join(", ")}${v[def].length === 1 ? "," : ""})`,
      `${def} = Literal[${v[def].map((x) => JSON.stringify(x)).join(", ")}]`,
      "",
    ]),
    `SigninReason = Literal[${[...v.AdmittedReason, ...v.RefusedReason].map((x) => JSON.stringify(x)).join(", ")}]`,
    "",
    `CLAIM_CODE_MAX_LENGTH = ${codeMax}`,
    `EMAIL_MAX_LENGTH = ${emailMax}`,
    "",
  ].join("\n");
  const ts = [
    `/** ${HEADER.replace("\n", "\n *  ")}`,
    " *",
    " *  The reason vocabularies of the sign-in admission wire (signin.v1), shared with admin-api's",
    " *  core/identity/services/admin-api/src/admin_api/app/signin_wire.py, generated from the same schema. */",
    "",
    ...VOCABS.flatMap(([constName, def]) => [
      `export const ${constName} = [${v[def].map((x) => JSON.stringify(x)).join(", ")}] as const;`,
      `export type ${def} = (typeof ${constName})[number];`,
      "",
    ]),
    "const member = <T extends string>(values: readonly T[]) => (x: unknown): x is T =>",
    "  typeof x === \"string\" && (values as readonly string[]).includes(x);",
    "",
    "export const isAdmittedReason = member(ADMITTED_REASONS);",
    "export const isRefusedReason = member(REFUSED_REASONS);",
    "export const isClaimReason = member(CLAIM_REASONS);",
    "",
    `export const CLAIM_CODE_MAX_LENGTH = ${codeMax};`,
    "",
  ].join("\n");
  return { python: py, typescript: ts };
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
      console.error(`signin.v1: generated file(s) out of date: ${stale.join(", ")} — run node core/identity/contracts/signin.v1/gen.mjs`);
      process.exit(1);
    }
    console.log("signin.v1: generated reason vocabularies match the schema");
  } else {
    const out = render();
    for (const [lang, path] of Object.entries(TARGETS)) writeFileSync(path, out[lang]);
    console.log(`signin.v1: wrote ${Object.values(TARGETS).map((p) => relative(ROOT, p)).join(", ")}`);
  }
}
