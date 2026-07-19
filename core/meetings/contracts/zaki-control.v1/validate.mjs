#!/usr/bin/env node
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "zaki-control.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);
const validateCallbackEnvelope = ajv.compile({ $ref: `${schema.$id}#/$defs/CallbackEnvelope` });
const CALLBACK_TEST_KEY = "zaki-control-v1-contract-test-key";

function semanticErrors(shape, data) {
  const errors = [];
  if (shape === "EnsureRequest" && data?.policy?.retention?.summary_days > data?.policy?.retention?.transcript_days) {
    errors.push("summary retention cannot outlive transcript retention");
  }
  if (shape === "CaptureRequest" && data?.capture_attestation?.attested_by_user_id !== data?.subject?.user_id) {
    errors.push("capture attestation user must match the bound subject");
  }
  if (shape === "CallbackVerificationVector") {
    const signedAt = Number(data?.headers?.["X-Webhook-Timestamp"]);
    if (!Number.isSafeInteger(signedAt) || Math.abs(data.received_at_unix - signedAt) > 300) {
      errors.push("callback signature is outside the 300-second replay window");
    }
    const expected = `sha256=${createHmac("sha256", CALLBACK_TEST_KEY)
      .update(`${data?.headers?.["X-Webhook-Timestamp"]}.${data?.raw_body}`)
      .digest("hex")}`;
    if (data?.headers?.["X-Webhook-Signature"] !== expected) {
      errors.push("callback signature does not authenticate timestamp.raw_body");
    }
    try {
      const body = JSON.parse(data.raw_body);
      if (!validateCallbackEnvelope(body)) errors.push("raw callback body does not conform to CallbackEnvelope");
    } catch {
      errors.push("raw callback body is not JSON");
    }
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

console.log(failed ? `zaki-control.v1: ${failed} golden(s) FAILED` : `zaki-control.v1: ${files.length} goldens discriminate`);
process.exit(failed ? 1 : 0);
