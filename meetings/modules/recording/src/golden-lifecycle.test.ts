/**
 * Golden conformance — the recording.v1 finalize LIFECYCLE (createRecordingAssembler),
 * not just the pure assembly. Each lifecycle vector is a SEQUENCE of recording.v1 frames
 * {seq, isFinal, bytes} + a finalize trigger ('is_final' | 'close'); driving them through
 * the assembler must reproduce the vector's master BYTE-FOR-BYTE.
 *
 * The `close`-finalized vectors pin the Stop-race invariant that reached production-test:
 * a session finalized by close() — when the trailing is_final chunk is lost — yields the
 * SAME master a clean is_final would. If close() ever stops finalizing, onMaster never
 * fires and this test fails LOUD (at L1, on both TS and the Python twin).
 *
 * Same vectors meeting-api (Python) finalizer is held to. Run: npx tsx src/golden-lifecycle.test.ts
 */
import { createHash } from "crypto";
import * as fs from "fs";
import * as path from "path";
import { createRecordingAssembler, type RecordingMaster, type RecordingMasterFormat } from "./recording-assembler";

const LIFE = path.resolve(__dirname, "contracts", "golden", "lifecycle");
const files = fs.existsSync(LIFE) ? fs.readdirSync(LIFE).filter((f) => f.endsWith(".json")).sort() : [];
if (files.length === 0) {
  console.error(`❌ no lifecycle vectors under ${LIFE} (run: node src/contracts/golden/generate.mjs)`);
  process.exit(1);
}

let failed = 0;
for (const f of files) {
  const v = JSON.parse(fs.readFileSync(path.join(LIFE, f), "utf8"));
  let got: RecordingMaster | null = null;
  const sink = createRecordingAssembler({ onMaster: (m) => { got = m; } });
  const key = `golden/${v.name}`;
  for (const fr of v.frames) {
    sink.chunk(key, fr.seq, fr.isFinal, v.format as RecordingMasterFormat, Buffer.from(fr.bytes, "base64"));
  }
  if (v.finalize === "close") sink.close(key);   // the close-without-is_final path — THE bug guard

  const m = got as RecordingMaster | null;
  const shaGot = m ? createHash("sha256").update(m.bytes).digest("hex") : "";
  const ok = !!m && m.bytes.length === v.master_len && shaGot === v.master_sha256;
  console.log(`  ${ok ? "✅" : "❌"} ${v.name}  (finalize=${v.finalize})`);
  if (!ok) {
    failed++;
    if (!m) console.log(`     onMaster never fired — finalize '${v.finalize}' did not assemble (THE BUG)`);
    else console.log(`     len ${m.bytes.length} vs ${v.master_len} · sha ${shaGot.slice(0, 12)} vs ${String(v.master_sha256).slice(0, 12)}`);
  }
}

if (failed) {
  console.error(`\n❌ golden-lifecycle: ${failed}/${files.length} vectors FAILED — the assembler lifecycle diverged from the golden.`);
  process.exit(1);
}
console.log(`\n✅ golden-lifecycle: all ${files.length} vectors pass — createRecordingAssembler ≡ the lifecycle golden (close-without-is_final included).`);
