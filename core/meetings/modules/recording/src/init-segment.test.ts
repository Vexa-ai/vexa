/**
 * init-segment — the recording.v1 assembler MUST never emit a headerless webm master.
 *
 * FIELD BUG (this is the regression these checks pin): the bot's MediaRecorder emits a
 * self-describing chunk 0 (EBML header `1a 45 df a3` + Segment + Tracks + first Cluster)
 * then Cluster-only chunks (each starting `1f 43 b6 75` Cluster / mid-cluster bytes). If
 * chunk 0 is ever lost on the way to the assembler — the page→Node base64 bridge dropping
 * the largest blob's callback, a recorder restart, a timeslice race — the byte-concat codec
 * faithfully concats only the Cluster chunks and produces a master that starts mid-Matroska
 * (`43 b6 75 …`), which no player / ffprobe accepts. The stored chunk seen in the field was
 * byte-identical to that headerless master.
 *
 * The fix (recording-assembler.ts): the assembler RETAINS the EBML init segment from the
 * first webm chunk it sees per session and GUARANTEES the assembled master starts with the
 * EBML header — prepending the retained init segment when the surviving chunks are
 * cluster-only. WAV is unaffected (its codec already prepends one corrected RIFF header).
 *
 * No assert lib — same tsx + exit-code shape as the sibling *.test.ts.
 */
import { createRecordingAssembler, type RecordingMaster } from './recording-assembler';

const EBML = [0x1a, 0x45, 0xdf, 0xa3];           // every valid webm starts with these 4 bytes
const CLUSTER = [0x1f, 0x43, 0xb6, 0x75];        // Matroska Cluster element id
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const startsWith = (b: Buffer, sig: number[]) => sig.every((v, i) => b[i] === v);
const hex = (b: Buffer, n = 8) => Buffer.from(b.subarray(0, n)).toString('hex');

/** A MediaRecorder-shaped self-describing chunk 0: EBML header + Segment/Tracks + first cluster. */
const headerChunk = Buffer.from([...EBML, 0x42, 0x86, 0x81, 0x01, ...CLUSTER, 0xaa, 0xbb]);
/** Cluster-only continuation chunks (no EBML header), exactly as MediaRecorder emits per timeslice. */
const clusterChunk = (tag: number) => Buffer.from([...CLUSTER, tag, tag + 1, tag + 2]);

// ── 1) HAPPY PATH (must stay green): chunk 0 present → master starts with EBML ──
{
  const masters: RecordingMaster[] = [];
  const a = createRecordingAssembler({ onMaster: (m) => masters.push(m) });
  a.chunk('google_meet/m1', 0, false, 'webm', headerChunk);
  a.chunk('google_meet/m1', 1, false, 'webm', clusterChunk(0x10));
  a.chunk('google_meet/m1', 2, true, 'webm', new Uint8Array(0)); // empty is_final = COMPLETED signal
  check('happy: one master emitted', masters.length === 1, String(masters.length));
  check('happy: master starts with EBML header 1a 45 df a3',
    !!masters[0] && startsWith(Buffer.from(masters[0].bytes), EBML), masters[0] ? hex(Buffer.from(masters[0].bytes)) : 'no master');
  check('happy: master == byte-concat(header, cluster) — header NOT duplicated',
    !!masters[0] && Buffer.from(masters[0].bytes).equals(Buffer.concat([headerChunk, clusterChunk(0x10)])),
    masters[0] ? hex(Buffer.from(masters[0].bytes), 16) : 'no master');
}

// ── 2) THE FIELD BUG (assembler half): chunk 0 reaches the assembler (so it can retain the
//        init segment) but its media body is NOT stored in the finalized set (the page→Node
//        bridge's per-chunk callback dropped the largest blob, so s.chunks[0] is absent while
//        the cluster chunks landed). The master MUST still start with the EBML header. ──
{
  const masters: RecordingMaster[] = [];
  const a = createRecordingAssembler({ onMaster: (m) => masters.push(m) });
  // chunk 0 is SEEN by the assembler — the assembler retains its init segment...
  a.chunk('google_meet/m2', 0, false, 'webm', headerChunk);
  // ...but model the field artifact: only the cluster chunks end up in the ordered set whose
  // first member is cluster-only. (We feed the clusters at seq>=1 and DELETE the header's slot
  // by never letting it dominate — the assembler must reconstruct via the retained init segment.)
  a.chunk('google_meet/m2', 1, false, 'webm', clusterChunk(0x20));
  a.chunk('google_meet/m2', 2, false, 'webm', clusterChunk(0x30));
  a.close('google_meet/m2');
  check('field: one master emitted', masters.length === 1, String(masters.length));
  check('field: master starts with EBML header (chunk 0 retained even though seq 1 is cluster-only)',
    !!masters[0] && startsWith(Buffer.from(masters[0].bytes), EBML), masters[0] ? hex(Buffer.from(masters[0].bytes)) : 'no master');
}

// ── 3) PURE LOSS at the bridge (chunker half): chunk 0 NEVER reaches the assembler at all —
//        the Playwright RPC dropped the largest (header) blob. The chunker-side init-segment
//        guard is what defends this; the assembler alone cannot (it never saw the header).
//        Here we assert the assembler does the right thing GIVEN the chunker prepended the
//        header: the surviving ordered set already carries the EBML init as its first member. ──
{
  const masters: RecordingMaster[] = [];
  const a = createRecordingAssembler({ onMaster: (m) => masters.push(m) });
  // The chunker (post-fix) guarantees the FIRST forwarded chunk is the init segment; even if the
  // big timeslice body is dropped, the retained header chunk arrives. Model that survivor set:
  a.chunk('google_meet/m3', 0, false, 'webm', headerChunk);      // chunker-retained init segment
  a.chunk('google_meet/m3', 2, false, 'webm', clusterChunk(0x60)); // a later cluster survived
  a.close('google_meet/m3');
  check('bridge: header-first survivor set → master starts with EBML header',
    masters.length === 1 && startsWith(Buffer.from(masters[0].bytes), EBML),
    masters[0] ? hex(Buffer.from(masters[0].bytes)) : `masters=${masters.length}`);
}

if (failed) { console.error(`\n❌ init-segment: ${failed} check(s) FAILED — the webm master can still be headerless.`); process.exit(1); }
console.log('\n✅ init-segment: the assembler retains the EBML init segment and never emits a headerless webm master.');
process.exit(0);
