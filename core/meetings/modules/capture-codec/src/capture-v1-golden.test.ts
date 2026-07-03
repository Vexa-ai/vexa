/**
 * capture.v1 golden conformance — the extension/bot → desktop capture wire.
 *
 * For every committed vector in src/contracts/golden/, assert BOTH directions
 * with byte-identity:
 *   encode  — encodeAudioFrame/encodeEvent(input) ≡ the golden bytes (len + sha256 + base64)
 *   decode  — decodeAudioFrame/decodeEvent(goldenBytes) deep-equals the input struct
 * PCM is compared Float32-bit-exactly (the wire stores Float32): the decoded
 * Float32Array must equal Float32Array.from(input.samples).
 *
 * The vectors are produced FROM THE CODEC by contracts/golden/generate.mjs, so a
 * pass here proves the committed wire bytes are exactly what the codec emits, and
 * that they round-trip. Source of truth: src/contracts/golden/.
 * Run: npx tsx src/capture-v1-golden.test.ts   (or: npm test)
 */
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { encodeAudioFrame, decodeAudioFrame, encodeEvent, decodeEvent, type MeetingEvent } from "./index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.resolve(HERE, "contracts", "golden");
const sha = (u8: Uint8Array) => createHash("sha256").update(Buffer.from(u8)).digest("hex");

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};
const eqBytes = (a: Uint8Array, b: Uint8Array) =>
  a.length === b.length && Buffer.compare(Buffer.from(a), Buffer.from(b)) === 0;
const eqF32 = (a: Float32Array, b: Float32Array) => {
  if (a.length !== b.length) return false;
  // Float32 bit-exact: compare the raw 4-byte representations (also pins NaN/sign).
  return Buffer.compare(Buffer.from(a.buffer, a.byteOffset, a.byteLength),
                        Buffer.from(b.buffer, b.byteOffset, b.byteLength)) === 0;
};

const files = fs.readdirSync(GOLDEN).filter((f) => f.endsWith(".json")).sort();
if (files.length === 0) {
  console.error(`❌ no golden vectors under ${GOLDEN} (run: npx tsx src/contracts/golden/generate.mjs)`);
  process.exit(1);
}

for (const f of files) {
  const v = JSON.parse(fs.readFileSync(path.join(GOLDEN, f), "utf8"));
  const golden = new Uint8Array(Buffer.from(v.bytes_b64, "base64"));

  // Byte-identity of the pinned bytes themselves (len + sha256 are redundant guards
  // over base64, but pin all three so a tampered field is caught precisely).
  check(`${v.name} · pinned len/sha`, golden.length === v.len && sha(golden) === v.sha256,
    `len=${golden.length}/${v.len} sha=${sha(golden).slice(0, 12)}/${String(v.sha256).slice(0, 12)}`);

  if (v.kind === "audio") {
    const expectF32 = Float32Array.from(v.samples as number[]);
    // ENCODE → must equal the golden bytes exactly.
    const enc = new Uint8Array(encodeAudioFrame(v.speakerIndex, v.ts, expectF32, v.speakerName));
    check(`${v.name} · encode ≡ golden bytes`, eqBytes(enc, golden),
      `len ${enc.length} sha ${sha(enc).slice(0, 12)} vs ${String(v.sha256).slice(0, 12)}`);
    // DECODE → must round-trip back to the input struct (PCM Float32-bit-exact).
    const dec = decodeAudioFrame(golden.buffer, golden.byteOffset, golden.byteLength);
    const nameOk = v.speakerName === undefined ? dec?.speakerName === undefined : dec?.speakerName === v.speakerName;
    const ok = !!dec && dec.speakerIndex === v.speakerIndex && dec.ts === v.ts && nameOk && eqF32(dec.samples, expectF32);
    check(`${v.name} · decode ≡ input struct`, ok, JSON.stringify({ ...dec, samples: dec ? Array.from(dec.samples) : null }));
  } else if (v.kind === "event") {
    const ev = v.event as MeetingEvent;
    // ENCODE → JSON.stringify(event) UTF-8 must equal the golden bytes.
    const enc = new TextEncoder().encode(encodeEvent(ev));
    check(`${v.name} · encode ≡ golden bytes`, eqBytes(enc, golden),
      `sha ${sha(enc).slice(0, 12)} vs ${String(v.sha256).slice(0, 12)}`);
    // DECODE → parse the golden bytes back to the event (deep-equal via canonical JSON).
    const dec = decodeEvent(Buffer.from(golden).toString("utf8"));
    check(`${v.name} · decode ≡ input event`, !!dec && JSON.stringify(dec) === JSON.stringify(ev), JSON.stringify(dec));
  } else {
    check(`${v.name} · known kind`, false, `unknown vector kind ${v.kind}`);
  }
}

if (failed) {
  console.error(`\n❌ capture.v1 golden: ${failed} assertion(s) FAILED — the codec diverged from the committed wire vectors.`);
  process.exit(1);
}
console.log(`\n✅ capture.v1 golden: all ${files.length} vectors pass — encode ≡ golden bytes AND decode ≡ input (both directions, byte-identical).`);
