#!/usr/bin/env node
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "zaki-read.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

// A collection page carries one order: `updated_at` descending, tie-broken by `id` descending.
// A consumer refuses the whole envelope when the update clock rises, so the order is contract.
function pageOrderErrors(items) {
  if (!Array.isArray(items)) return [];

  const errors = [];
  for (const [index, item] of items.entries()) {
    if (index === 0) continue;
    const prior = items[index - 1];
    const updated = Date.parse(item.updated_at);
    const priorUpdated = Date.parse(prior.updated_at);
    if (!Number.isFinite(updated) || !Number.isFinite(priorUpdated)) continue;
    if (updated > priorUpdated) {
      errors.push(`item ${index} rises in updated_at above the preceding item`);
    } else if (updated === priorUpdated && item.id > prior.id) {
      errors.push(`item ${index} breaks the id-descending tie order`);
    }
  }
  return errors;
}

function semanticErrors(shape, data) {
  if (shape === "IndexResponse") return pageOrderErrors(data?.items);
  if (shape !== "ItemResponse" || data?.item?.kind !== "transcript") return [];
  const turns = data.item.content?.turns;
  if (!Array.isArray(turns)) return [];

  const errors = [];
  let priorStart = Number.NEGATIVE_INFINITY;
  for (const [index, turn] of turns.entries()) {
    const start = Date.parse(turn.started_at);
    const end = turn.ended_at === undefined ? null : Date.parse(turn.ended_at);
    if (Number.isFinite(start) && start < priorStart) {
      errors.push(`turn ${index} starts before the preceding turn`);
    }
    if (Number.isFinite(start) && Number.isFinite(end) && end < start) {
      errors.push(`turn ${index} ends before it starts`);
    }
    if (Number.isFinite(start)) priorStart = start;
  }
  return errors;
}

let failed = 0;
const files = readdirSync(join(HERE, "golden")).filter((name) => name.endsWith(".json"));
for (const file of files) {
  const shape = file.split(".")[0];
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(HERE, "golden", file), "utf8"));
  const schemaValid = validate(data);
  const semantics = schemaValid ? semanticErrors(shape, data) : [];
  const valid = schemaValid && semantics.length === 0;
  const expected = !file.includes(".invalid-");
  if (valid === expected) {
    console.log(`  ✓ ${file} ${expected ? "conforms" : "is rejected"}`);
  } else {
    const detail = semantics.length ? semantics.join("; ") : ajv.errorsText(validate.errors);
    console.error(`  ✗ ${file} ${expected ? "failed" : "was accepted"}: ${detail}`);
    failed += 1;
  }
}

console.log(failed ? `zaki-read.v1: ${failed} golden(s) FAILED` : `zaki-read.v1: ${files.length} goldens discriminate`);
process.exit(failed ? 1 : 0);
