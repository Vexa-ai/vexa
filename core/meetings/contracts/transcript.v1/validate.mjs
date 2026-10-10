#!/usr/bin/env node
/**
 * gate:schema for transcript.v1 — validate every golden against transcript.schema.json.
 * Convention: a golden filename is `<Shape>.<case>.json`; the part before the first dot is the
 * `$def` it must conform to (e.g. `TranscriptSegment.glow.json` → #/$defs/TranscriptSegment).
 * Two shapes are checked past the schema, in a second language:
 *   SignedEntryVector — re-signed here: auth = the token's header.payload, sig = hex HMAC-SHA256 of
 *                       the payload keyed with the whole token (segment_entry.py, transcript-redis.ts).
 *   FeedEntry         — its payload is parsed and must be a FeedTranscription, a FeedRetract or a
 *                       SessionEnd, by its `type`.
 * Run: node validate.mjs [--check]
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHmac } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "transcript.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);

const dir = join(HERE, "golden");
const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
let failed = 0;
for (const f of files) {
  const shape = f.split(".")[0];
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${shape}` });
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  if (!validate(data)) { console.error(`  ✗ ${f} (${shape}): ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  if (shape === "SignedEntryVector") {
    const [h, c] = data.token.split(".");
    const sig = createHmac("sha256", Buffer.from(data.token, "utf8")).update(Buffer.from(data.payload, "utf8")).digest("hex");
    if (data.auth !== `${h}.${c}` || data.sig !== sig) { console.error(`  ✗ ${f}: re-signing does not reproduce auth/sig`); failed++; continue; }
  }
  if (shape === "FeedEntry") {
    let inner; try { inner = JSON.parse(data.payload); } catch { inner = null; }
    const as = { transcription: "FeedTranscription", retract: "FeedRetract", session_start: "FeedSessionStart", session_end: "SessionEnd" }[inner?.type];
    const check = as && ajv.compile({ $ref: `${schema.$id}#/$defs/${as}` });
    if (!check || !check(inner)) { console.error(`  ✗ ${f}: payload is not a feed entry${check ? ": " + ajv.errorsText(check.errors) : ""}`); failed++; continue; }
  }
  console.log(`  ✓ ${f} ≡ ${shape}`);
}
console.log(failed ? `transcript.v1: ${failed} golden(s) FAILED` : `transcript.v1: ${files.length} goldens conform`);
process.exit(failed ? 1 : 0);
