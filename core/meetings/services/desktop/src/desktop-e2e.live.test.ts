/**
 * Live e2e: the desktop HOST end-to-end against the REAL transcription service. Start the host on
 * ephemeral ports, feed known TTS clips as capture.v1 over the ingest WS (round-tripping the wire
 * codec), read /transcripts from the gateway, and assert glow-attributed transcript.v1 conforming to
 * the sealed schema. Validates the whole composition: capture-codec → ingest → gmeet-pipeline → real
 * STT → store → gateway. Skips without VEXA_TX_KEY + EVAL_CACHE (the TTS clip pool).
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocket } from "ws";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { encodeAudioFrame } from "@vexa/capture-codec";
import { startDesktop } from "./desktop.js";

const KEY = process.env.VEXA_TX_KEY;
const URL = process.env.VEXA_TX_URL || "https://transcription.vexa.ai";
const CACHE = process.env.EVAL_CACHE;
const SPK = [{ key: "B", name: "Boris" }, { key: "D", name: "Dmitry" }, { key: "C", name: "Galina" }, { key: "V", name: "Vera" }];

if (!KEY || !CACHE || !SPK.every((s) => existsSync(join(CACHE, `${s.key}.json`)))) {
  console.log("⏭  SKIP desktop-e2e.live — needs VEXA_TX_KEY + EVAL_CACHE (TTS clip pool)");
  process.exit(0);
}

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(resolve(here, "../../../contracts/transcript.v1/transcript.schema.json"), "utf-8"));
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
const validateSeg = ajv.compile({ $schema: "https://json-schema.org/draft/2020-12/schema", $defs: schema.$defs, $ref: "#/$defs/TranscriptSegment" });

const wav = (b64: string) => { const b = Buffer.from(b64, "base64"); const n = (b.length - 44) >> 1; const f = new Float32Array(n); for (let i = 0; i < n; i++) f[i] = b.readInt16LE(44 + i * 2) / 32768; return f; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name: string, cond: boolean, detail = "") => { console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`); if (!cond) failed++; };

async function main() {
  const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, txUrl: URL, txToken: KEY!, quiet: true });
  const platform = "google_meet", native = "desk-e2e";
  try {
    const ws = new WebSocket(`ws://localhost:${desk.ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}&language=en`);
    await new Promise<void>((res, rej) => { ws.on("open", () => res()); ws.on("error", rej); });
    // No wait for the 'ready' message — the host attaches its frame handler synchronously on connect,
    // so frames sent right after open are received; waiting for 'ready' races with its arrival.

    let ts = 0;                                       // each speaker = a gmeet channel, glow-named
    for (let ch = 0; ch < SPK.length; ch++) {
      const clip = JSON.parse(readFileSync(join(CACHE!, `${SPK[ch].key}.json`), "utf-8"))[0];
      const pcm = wav(clip.b64);
      for (let o = 0; o < pcm.length; o += 8000) { ws.send(Buffer.from(encodeAudioFrame(ch, ts, pcm.subarray(o, o + 8000), SPK[ch].name))); ts += 500; }
    }
    ws.close();                                       // → host finish() → pipe.dispose() → final STT submit → store

    let segs: any[] = [];
    for (let i = 0; i < 60; i++) {                     // poll the gateway until segments land (real STT latency)
      await sleep(1000);
      const r = await fetch(`http://localhost:${desk.gatewayPort}/transcripts/${platform}/${native}`);
      segs = (await r.json()).segments || [];
      if (segs.length >= SPK.length) break;
    }

    for (const s of segs) console.log(`     [${s.speaker}] ${(s.text || "").slice(0, 80)}`);
    const chOf = (k?: string) => { const m = /^ch-(\d+):/.exec(k || ""); return m ? SPK[Number(m[1])]?.name : undefined; };
    check("host served transcript.v1 over the gateway", segs.length > 0, `${segs.length}`);
    check("completeness — every speaker produced an attributed segment", SPK.every((s) => segs.some((g) => g.speaker === s.name && (g.text || "").trim())));
    check("every served segment conforms to the SEALED transcript.v1", segs.every((s) => validateSeg(s) as boolean));
    check("attribution — every segment glow-bound to its channel speaker", segs.every((s) => s.source === "glow-bound" && s.speaker === chOf(s.speaker_key)));
  } finally {
    await desk.close();
  }

  if (failed) { console.error(`\n❌ desktop-e2e.live: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log(`\n✅ desktop-e2e.live: capture.v1 → ingest → gmeet-pipeline → REAL STT → gateway /transcripts — glow-attributed, schema-valid.`);
  process.exit(0);   // force exit — don't hang on any lingering socket handle
}

main().catch((e) => { console.error(e); process.exit(1); });
