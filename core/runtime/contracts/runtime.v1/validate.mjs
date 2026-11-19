#!/usr/bin/env node
/**
 * gate:schema (level 1) for runtime.v1 — validate every golden vector against runtime.schema.json.
 * The goldens are the spec (P8). Golden filenames start with the shape: spec- / status- / event- / error-
 * / signed-. A signed- vector is also re-signed here (CallbackSignature), a second derivation of the
 * runtime's signer and meeting-api's verifier.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "runtime.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const SHAPE = { spec: "WorkloadSpec", status: "WorkloadStatus", event: "RuntimeEvent", error: "Error", signed: "SignedEventVector" };
const CALLBACK_LABEL = "vexa-runtime-callback.v2";
// json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False): sorted at every depth, UTF-8.
const canon = (v) => Array.isArray(v) ? `[${v.map(canon).join(",")}]`
  : v && typeof v === "object" ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`
  : JSON.stringify(v);
// The signature covers "<timestamp>\n<url>\n" + canonical(event).
const signCallback = (token, url, timestamp, event) => {
  const key = createHmac("sha256", Buffer.from(token, "utf8")).update(CALLBACK_LABEL).digest();
  const message = Buffer.from(`${timestamp}\n${url}\n${canon(event)}`, "utf8");
  return `t=${timestamp},v2=` + createHmac("sha256", key).update(message).digest("hex");
};
const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const shape = SHAPE[f.split("-")[0]];
  if (!shape) { console.error(`  ✗ ${f}: filename must start with spec- / status- / event- / error- / signed-`); failed++; continue; }
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (!validate(data)) { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  if (shape === "SignedEventVector" && signCallback(data.token, data.url, data.timestamp, data.event) !== data.signature) {
    console.error(`  ✗ ${f}: re-signing the event does not reproduce the signature`); failed++; continue;
  }
  console.log(`  ✓ ${f} ≡ ${shape}`);
}
console.log(failed ? `runtime.v1: ${failed} golden(s) FAILED` : `runtime.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
