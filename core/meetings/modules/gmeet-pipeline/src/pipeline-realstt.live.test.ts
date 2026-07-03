/**
 * Live e2e: the gmeet spine against the REAL transcription service. Feed known TTS clips through the
 * channel-routed pipeline + real STT (transcription.vexa.ai) and assert it emits glow-attributed
 * transcript.v1 conforming to the sealed schema. The offline contract is pinned by
 * pipeline-conformance.test.ts (stub STT); this adds the REAL backend + real audio.
 *
 * Skipped without VEXA_TX_KEY + EVAL_CACHE (the TTS clip pool, e.g. ~/vexa-test-rig/cache) — same
 * skip-where-no-backend pattern as the runtime docker/k8s tests. turbo passes those env through.
 * Run: VEXA_TX_KEY=… EVAL_CACHE=~/vexa-test-rig/cache npx tsx src/pipeline-realstt.live.test.ts
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from "./index.js";
import { TranscriptionClient } from "@vexa/transcribe-whisper";

const KEY = process.env.VEXA_TX_KEY;
const URL = process.env.VEXA_TX_URL || "https://transcription.vexa.ai";
const CACHE = process.env.EVAL_CACHE;
const SPK = [{ key: "B", name: "Boris" }, { key: "D", name: "Dmitry" }, { key: "C", name: "Galina" }, { key: "V", name: "Vera" }];

if (!KEY || !CACHE || !SPK.every((s) => existsSync(join(CACHE, `${s.key}.json`)))) {
  console.log("⏭  SKIP pipeline-realstt.live — needs VEXA_TX_KEY + EVAL_CACHE (TTS clip pool)");
  process.exit(0);
}

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(resolve(here, "../../../contracts/transcript.v1/transcript.schema.json"), "utf-8"));
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
const validateSeg = ajv.compile({ $schema: "https://json-schema.org/draft/2020-12/schema", $defs: schema.$defs, $ref: "#/$defs/TranscriptSegment" });

const wav = (b64: string) => {                     // 16kHz mono 16-bit WAV → Float32Array
  const b = Buffer.from(b64, "base64");
  const n = (b.length - 44) >> 1;
  const f = new Float32Array(n);
  for (let i = 0; i < n; i++) f[i] = b.readInt16LE(44 + i * 2) / 32768;
  return f;
};

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};

async function run() {
  const client = new TranscriptionClient({ serviceUrl: URL, apiToken: KEY! });
  const segments: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => segments.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({ transcribe: (pcm, prompt) => client.transcribe(pcm, "en", prompt), sink });

  let ts = 0;                                       // each speaker = a gmeet channel, glow-named
  for (let ch = 0; ch < SPK.length; ch++) {
    const clip = JSON.parse(readFileSync(join(CACHE!, `${SPK[ch].key}.json`), "utf-8"))[0];
    const pcm = wav(clip.b64);
    for (let o = 0; o < pcm.length; o += 8000) { pipe.feedAudio(ch, SPK[ch].name, pcm.subarray(o, o + 8000), ts); ts += 500; }
  }
  await pipe.flush();
  await pipe.dispose();

  for (const s of segments) console.log(`     [${s.speaker}] ${(s.text || "").slice(0, 80)}`);

  const chOf = (k?: string) => { const m = /^ch-(\d+):/.exec(k || ""); return m ? SPK[Number(m[1])]?.name : undefined; };
  check("real STT produced transcript.v1 segments", segments.length > 0, `${segments.length}`);
  check("completeness — every speaker produced an attributed segment",
    SPK.every((s) => segments.some((g) => g.speaker === s.name && (g.text || "").trim())));
  check("every segment conforms to the SEALED transcript.v1", segments.every((s) => validateSeg(s) as boolean));
  check("attribution — every segment glow-bound to its channel's speaker",
    segments.every((s) => s.source === "glow-bound" && s.speaker === chOf(s.speaker_key)));

  if (failed) { console.error(`\n❌ pipeline-realstt.live: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log(`\n✅ pipeline-realstt.live: gmeet spine + REAL STT → ${segments.length} glow-attributed transcript.v1 segments, all schema-valid.`);
}

run().catch((e) => { console.error(e); process.exit(1); });
