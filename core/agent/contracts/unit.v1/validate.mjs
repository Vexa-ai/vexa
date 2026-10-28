#!/usr/bin/env node
/** gate:schema for unit.v1 — golden `<Shape>.<case>.json` validates against #/$defs/<Shape>.
 *  An InputVector is also re-derived here: key = hex HMAC-SHA256(secret, "vexa-unit-input.v1:" + unit
 *  id), sig = hex HMAC-SHA256(key bytes, turn) — shared/unit_input.py, restated in a second language.
 *  The FAULT half: a `Fault` golden's case is `<source>.<kind>` and there is one per kind of every
 *  source, the known-bad faults are refused, and the vocabularies `gen-faults.mjs` writes for the
 *  worker, agent-api and the terminal still match the schema. */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { drift, vocab } from "./gen-faults.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "unit.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
const UNIT_INPUT_LABEL = "vexa-unit-input.v1:";
const hex = (key, msg) => createHmac("sha256", key).update(Buffer.from(msg, "utf8")).digest("hex");
for (const f of files) {
  const shape = f.split(".")[0];
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (!validate(data)) { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  if (shape === "Fault") {
    const [, source, kind] = f.replace(/\.json$/, "").split(".");
    if (data.source !== source || data.kind !== kind) {
      console.error(`  ✗ ${f}: names ${source}/${kind} but holds ${data.source}/${data.kind}`); failed++; continue;
    }
  }
  if (shape === "InputVector") {
    const key = hex(Buffer.from(data.secret, "utf8"), UNIT_INPUT_LABEL + data.unit_id);
    if (key !== data.key || hex(Buffer.from(key, "hex"), data.turn) !== data.sig) {
      console.error(`  ✗ ${f}: re-deriving the unit key or the signature does not reproduce the vector`); failed++; continue;
    }
  }
  console.log(`  ✓ ${f} ≡ ${shape}`);
}
// ── the fault vocabulary ──────────────────────────────────────────────────────────────────────
// One golden per (source, kind): a kind added to the schema without a golden is a kind nobody pinned.
const { sources, kinds } = vocab(schema);
for (const s of sources) for (const k of kinds[s]) {
  if (!files.includes(`Fault.${s}.${k}.json`)) { console.error(`  ✗ no golden Fault.${s}.${k}.json for ${s} kind "${k}"`); failed++; }
}
// Faults the wire must refuse — what a renderer could not attribute, or would attribute wrongly.
const shape = (name) => ajv.compile({ $ref: `${schema.$id}#/$defs/${name}` });
const MUST_FAIL = [
  ["Fault", { source: "runtime", kind: "unpaid", op: "spawn" }, "a kind from another source's vocabulary"],
  ["Fault", { source: "model-provider", kind: "spawn_refused", provider: "x", model: "" }, "a runtime kind on the model provider"],
  ["Fault", { source: "runtime", kind: "spawn_refused" }, "a runtime fault that does not name its op"],
  ["Fault", { source: "model-provider", kind: "unpaid" }, "a provider fault that does not name the provider"],
  ["Fault", { source: "vexa-tools", kind: "access_expired", op: "spawn" }, "an op on a fault that is not the runtime's"],
  ["Fault", { source: "gateway", kind: "unreachable", provider: "x" }, "a provider on a fault that is not the provider's"],
  ["Fault", { source: "something", kind: "failed" }, "a source the contract does not know"],
  ["Fault", { kind: "unpaid", provider: "x", model: "" }, "a fault that names nobody"],
  ["Fault", { source: "agent-api", kind: "internal", upstream: "kubectl: pod already exists" }, "a field the contract does not carry (the runtime's own words)"],
  ["Fault", { source: "agent-api", kind: "internal", status: 99 }, "a status that is not an HTTP status"],
  ["DoneFrame", { type: "done", reply: "", sessionId: null, ok: true,
                  fault: { source: "vexa-tools", kind: "access_expired" } }, "a successful turn that carries a fault"],
  ["DispatchRefusal", { detail: "the runtime could not start your agent" }, "a refusal with no typed fault"],
];
for (const [name, body, why] of MUST_FAIL) {
  if (shape(name)(body)) { console.error(`  ✗ ${name} accepted ${why}: ${JSON.stringify(body)}`); failed++; }
  else console.log(`  ✓ ${name} refuses ${why}`);
}
const stale = drift();
for (const p of stale) { console.error(`  ✗ ${p} is out of date — run node core/agent/contracts/unit.v1/gen-faults.mjs`); failed++; }
if (!stale.length) console.log("  ✓ generated fault vocabularies match the schema");

console.log(failed ? `unit.v1: ${failed} check(s) FAILED` : `unit.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
