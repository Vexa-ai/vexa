/**
 * Golden conformance — the desktop (TypeScript) master builder must reproduce
 * the shared recording.v1 golden vectors byte-for-byte.
 *
 * These are the SAME vectors meeting-api (Python) is tested against
 * (services/meeting-api/tests/test_recording_golden.py). A pass on both = the two
 * deliberate implementations are provably in sync. Source of truth:
 * src/contracts/golden/. Run: npm test (or npx tsx src/golden.test.ts).
 */
import { createHash } from "crypto";
import * as fs from "fs";
import * as path from "path";
import { buildRecordingMaster } from "./recording-codec";

const GOLDEN = path.resolve(__dirname, "contracts", "golden");

const files = fs.readdirSync(GOLDEN).filter((f) => f.endsWith(".json")).sort();
if (files.length === 0) {
  console.error(`❌ no golden vectors under ${GOLDEN} (run: node modules/recording/src/contracts/golden/generate.mjs)`);
  process.exit(1);
}

let failed = 0;
for (const f of files) {
  const v = JSON.parse(fs.readFileSync(path.join(GOLDEN, f), "utf8"));
  const chunks: Buffer[] = v.chunks.map((c: string) => Buffer.from(c, "base64"));
  const master = buildRecordingMaster(v.format, chunks);
  const sha = createHash("sha256").update(master).digest("hex");
  const ok = master.length === v.master_len && sha === v.master_sha256;
  console.log(`  ${ok ? "✅" : "❌"} ${v.name}`);
  if (!ok) {
    failed++;
    console.log(`     len ${master.length} vs ${v.master_len} · sha ${sha.slice(0, 12)} vs ${String(v.master_sha256).slice(0, 12)}`);
  }
}

if (failed) {
  console.error(`\n❌ golden: ${failed}/${files.length} vectors FAILED — TypeScript diverged from the golden (and from Python).`);
  process.exit(1);
}
console.log(`\n✅ golden: all ${files.length} vectors pass — TS master builder ≡ golden (≡ Python).`);
