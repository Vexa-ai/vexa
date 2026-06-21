/**
 * The gmeet-pipeline REPLAY golden (Stage 3.2's gate) — drive the channel-routed
 * pipeline OFFLINE with a stub Whisper (no transcription-service), and prove every
 * emitted segment conforms to the SEALED transcript.v1 schema. This binds the
 * pipeline's output to the contract SSOT — no live meeting, fully deterministic.
 * Run: npm test (chained)  or  npx tsx src/pipeline-conformance.test.ts
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from "./index.js";
import type { TranscriptionResult } from "@vexa/transcribe-whisper";

const here = dirname(fileURLToPath(import.meta.url));

// Compile a validator for TranscriptSegment straight from the SEALED schema's $defs.
const schema = JSON.parse(
  readFileSync(resolve(here, "../../../contracts/transcript.v1/transcript.schema.json"), "utf-8"),
);
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
const validateSegment = ajv.compile({
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $defs: schema.$defs,
  $ref: "#/$defs/TranscriptSegment",
});

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};

// A deterministic stub Whisper — the offline substitute for the transcription-service.
const transcribe = async (_pcm: Float32Array, _prompt?: string): Promise<TranscriptionResult> => ({
  text: "hello world this is a test",
  language: "en",
  language_probability: 0.99,
  duration: 1,
  segments: [{ start: 0, end: 1, text: "hello world this is a test" }],
});

async function run() {
  const segments: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => segments.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({ transcribe, sink });

  const ONE_SEC = new Float32Array(16000).fill(0.1);
  pipe.feedAudio(0, "Alice", ONE_SEC, 0);     // named by glow
  pipe.feedAudio(1, "Bob", ONE_SEC, 0);       // a second speaker, separate channel (overlap-safe)
  pipe.feedAudio(2, undefined, ONE_SEC, 0);   // onset with no single glow → provisional
  await pipe.flush();
  await pipe.dispose();

  check("a segment per channel emitted", segments.length === 3, `got ${segments.length}`);

  let allConform = true;
  for (const s of segments) {
    if (!validateSegment(s)) {
      allConform = false;
      console.log("    ✗ non-conforming:", JSON.stringify(s), JSON.stringify(validateSegment.errors));
    }
  }
  check("every segment conforms to SEALED transcript.v1 (TranscriptSegment)", allConform);

  const by = Object.fromEntries(segments.map((s) => [s.speaker, s]));
  check("glow names carried through (Alice + Bob)", !!by["Alice"] && !!by["Bob"]);
  check("glow-bound segment: source=glow-bound, confidence=1, completed=true",
    by["Alice"]?.source === "glow-bound" && by["Alice"]?.confidence === 1 && by["Alice"]?.completed === true,
    JSON.stringify(by["Alice"]));
  check("unglowed onset → 'Speaker', source=provisional-cluster-id, confidence=0",
    !!by["Speaker"] && by["Speaker"]?.source === "provisional-cluster-id" && by["Speaker"]?.confidence === 0,
    JSON.stringify(by["Speaker"]));
  check("sealed snake_case shape: segment_id + speaker_key strings",
    segments.every((s) => typeof s.segment_id === "string" && typeof s.speaker_key === "string"));

  if (failed) { console.error(`\n❌ pipeline-conformance: ${failed} checks FAILED.`); process.exit(1); }
  console.log(`\n✅ pipeline-conformance: gmeet pipeline emits SEALED transcript.v1 offline — names carried, source/confidence correct, every segment schema-valid.`);
}

run().catch((e) => { console.error(e); process.exit(1); });
