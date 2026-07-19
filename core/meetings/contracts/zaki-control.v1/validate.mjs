#!/usr/bin/env node
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "zaki-control.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);
const validateCallbackEnvelope = ajv.compile({ $ref: `${schema.$id}#/$defs/CallbackEnvelope` });
const mutationOperations = [
  ["ensure", "EnsureRequest"],
  ["capture", "CaptureRequest"],
  ["stop_capture", "StopCaptureRequest"],
  ["erase_meeting", "EraseMeetingRequest"],
  ["erase_account", "EraseAccountRequest"]
].map(([operation, shape]) => [
  operation,
  shape,
  ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` })
]);
const CALLBACK_TEST_KEY = "zaki-control-v1-contract-test-key";

function meetingUrlMatchesPlatform(platform, rawUrl, configuredJitsiHosts = []) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" || url.username || url.password) return false;

  const host = url.hostname.toLowerCase();
  if (platform === "google_meet") {
    if (host !== "meet.google.com" || url.pathname.startsWith("/lookup/")) return false;
    const code = url.pathname.split("/").filter(Boolean)[0] ?? "";
    return /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(code) || /^[a-z0-9][a-z0-9-]{3,38}[a-z0-9]$/.test(code);
  }
  if (platform === "zoom") {
    const zoomHost = host === "zoom.us" || host.endsWith(".zoom.us") ||
      host === "zoomgov.com" || host.endsWith(".zoomgov.com");
    if (!zoomHost) return false;
    return /^\/(?:j|w)\/\d{9,11}\/?$/.test(url.pathname) || /^\/wc\/join\/\d{9,11}\/?$/.test(url.pathname);
  }
  if (platform === "teams") {
    const teamsHost = host === "teams.live.com" || host.endsWith(".teams.live.com") ||
      host === "teams.microsoft.com" || host.endsWith(".teams.microsoft.com") ||
      host === "gov.teams.microsoft.us" || host === "dod.teams.microsoft.us" ||
      host.endsWith(".teams.microsoft.us");
    if (!teamsHost) return false;
    let fragmentPath = "";
    try {
      fragmentPath = new URL(`https://x${url.hash.slice(1)}`).pathname;
    } catch {
      fragmentPath = "";
    }
    return /^\/meet\/\d{10,15}\/?$/.test(url.pathname) ||
      url.pathname.includes("/l/meetup-join/") ||
      (url.pathname.replace(/\/$/, "") === "/v2" && /^\/meet\/\d{10,15}\/?$/.test(fragmentPath));
  }
  if (platform === "jitsi") {
    const configuredHosts = new Set(configuredJitsiHosts.map((value) => value.toLowerCase()));
    const knownHost = host === "meet.jit.si" || configuredHosts.has(host) || host.includes("jitsi") || host.split(".").includes("meet");
    const room = url.pathname.replace(/^\/+|\/+$/g, "");
    return knownHost && room.length > 0 && !/[/?#\s]/.test(room);
  }
  return false;
}

function sameArray(actual, expected) {
  return actual.length === expected.length && actual.every((value, index) => value === expected[index]);
}

function sortJson(value) {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortJson(value[key])]));
  }
  return value;
}

function canonicalRequestSha256(request) {
  const { request_id: _requestId, idempotency_key: _idempotencyKey, ...semanticRequest } = request;
  return createHash("sha256")
    .update(JSON.stringify(sortJson(semanticRequest)), "utf8")
    .digest("hex");
}

function mutationOperation(request) {
  const matches = mutationOperations.filter(([, , validate]) => validate(request));
  return matches.length === 1 ? matches[0] : undefined;
}

function usageSettlementErrors(data) {
  const errors = [];
  const applied = [];
  const ignored = [];
  const seenEventIds = new Set();
  const appliedSequences = new Map();
  let identity;
  let finalSequence = 0;
  let capturedSecondsTotal = 0;
  let terminal = false;

  for (const event of data.events) {
    if (event.event_type !== "minutes.capture.usage") {
      errors.push("usage settlement vectors may contain only minutes.capture.usage events");
      continue;
    }
    const currentIdentity = JSON.stringify({
      subject: event.data.subject,
      operation_id: event.data.operation_id,
      capture_id: event.data.capture_id,
      meeting_id: event.data.meeting_id,
      reservation_id: event.data.metering.reservation_id
    });
    if (identity === undefined) identity = currentIdentity;
    else if (identity !== currentIdentity) errors.push("usage settlement events must share one subject and metering identity");

    if (seenEventIds.has(event.event_id) || terminal) {
      ignored.push(event.event_id);
      continue;
    }
    seenEventIds.add(event.event_id);

    const { sequence, captured_seconds_total: total, terminal: eventTerminal } = event.data.metering;
    const priorAtSequence = appliedSequences.get(sequence);
    if (priorAtSequence) {
      if (priorAtSequence.total !== total || priorAtSequence.terminal !== eventTerminal) {
        errors.push(`usage sequence ${sequence} conflicts with an already applied cumulative value`);
      }
      ignored.push(event.event_id);
      continue;
    }
    if (sequence < finalSequence) {
      if (total > capturedSecondsTotal) errors.push(`stale usage sequence ${sequence} exceeds the applied cumulative total`);
      ignored.push(event.event_id);
      continue;
    }
    if (total < capturedSecondsTotal) {
      errors.push(`usage total decreases at sequence ${sequence}`);
      ignored.push(event.event_id);
      continue;
    }

    applied.push(event.event_id);
    appliedSequences.set(sequence, { total, terminal: eventTerminal });
    finalSequence = sequence;
    capturedSecondsTotal = total;
    terminal = eventTerminal;
  }

  const expected = data.expected;
  if (!sameArray(applied, expected.applied_event_ids)) errors.push("applied usage event identities do not match expected settlement");
  if (!sameArray(ignored, expected.ignored_event_ids)) errors.push("ignored usage event identities do not match expected settlement");
  if (finalSequence !== expected.final_sequence) errors.push("final usage sequence does not match expected settlement");
  if (capturedSecondsTotal !== expected.final_captured_seconds_total) errors.push("final cumulative usage does not match expected settlement");
  if (terminal !== expected.terminal) errors.push("terminal settlement state does not match expected settlement");
  return errors;
}

function idempotencyReplayErrors(data) {
  const errors = [];
  const records = new Map();
  const outcomes = data.attempts.map((attempt) => {
    const request = attempt.request;
    const mutation = mutationOperation(request);
    if (!mutation) {
      errors.push("idempotency attempt must contain exactly one recognized mutation request");
      return "invalid";
    }
    const [operation, shape] = mutation;
    const requestErrors = semanticErrors(shape, request);
    if (requestErrors.length) {
      errors.push(...requestErrors.map((error) => `idempotency attempt: ${error}`));
      return "invalid";
    }
    const namespace = [
      request.api_version,
      request.subject.tenant_id,
      request.subject.user_id,
      operation,
      request.idempotency_key
    ].join("\u0000");
    const requestHash = canonicalRequestSha256(request);
    const previousHash = records.get(namespace);
    if (previousHash === undefined) {
      records.set(namespace, requestHash);
      return "applied";
    }
    return previousHash === requestHash ? "replayed" : "conflict";
  });
  if (!sameArray(outcomes, data.expected_outcomes)) {
    errors.push("idempotency outcomes do not match canonical owner/operation-scoped replay semantics");
  }
  return errors;
}

function captureRequestErrors(request, configuredJitsiHosts = []) {
  const errors = [];
  if (request?.capture_attestation?.attested_by_user_id !== request?.subject?.user_id) {
    errors.push("capture attestation user must match the bound subject");
  }
  if (!meetingUrlMatchesPlatform(request?.platform, request?.meeting_url, configuredJitsiHosts)) {
    errors.push("meeting URL must be a supported HTTPS URL matching the declared platform and validation context");
  }
  return errors;
}

function semanticErrors(shape, data) {
  const errors = [];
  if (shape === "EnsureRequest" && data?.policy?.retention?.summary_days > data?.policy?.retention?.transcript_days) {
    errors.push("summary retention cannot outlive transcript retention");
  }
  if (shape === "CaptureRequest") errors.push(...captureRequestErrors(data));
  if (shape === "CaptureRequestValidationVector") {
    errors.push(...captureRequestErrors(data?.request, data?.configured_jitsi_hosts));
  }
  if (shape === "CallbackVerificationVector") {
    const signedAt = Number(data?.headers?.["X-Webhook-Timestamp"]);
    if (!Number.isSafeInteger(signedAt) || Math.abs(data.received_at_unix - signedAt) > 300) {
      errors.push("callback signature is outside the 300-second replay window");
    }
    const expected = `sha256=${createHmac("sha256", CALLBACK_TEST_KEY)
      .update(`${data?.headers?.["X-Webhook-Timestamp"]}.${data?.raw_body}`)
      .digest("hex")}`;
    const actualBuffer = Buffer.from(data?.headers?.["X-Webhook-Signature"] ?? "");
    const expectedBuffer = Buffer.from(expected);
    if (actualBuffer.length !== expectedBuffer.length || !timingSafeEqual(actualBuffer, expectedBuffer)) {
      errors.push("callback signature does not authenticate timestamp.raw_body");
    }
    try {
      const body = JSON.parse(data.raw_body);
      if (!validateCallbackEnvelope(body)) errors.push("raw callback body does not conform to CallbackEnvelope");
    } catch {
      errors.push("raw callback body is not JSON");
    }
  }
  if (shape === "UsageSettlementVector") errors.push(...usageSettlementErrors(data));
  if (shape === "IdempotencyReplayVector") errors.push(...idempotencyReplayErrors(data));
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
