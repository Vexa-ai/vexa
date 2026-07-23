/**
 * L3 — recording sink (recording.v1 PER-CHUNK durable upload). OFFLINE, NO disk (HTTP only to a
 * localhost capture server for the wire arm).
 *
 * #491/#412: the 0.12 bot accumulated the whole recording in Node memory and uploaded ONE master at
 * graceful close — a SIGKILL lost everything. This sink uploads EACH timeslice immediately. This
 * test is the P22/#224-class regression pin that absence lacked. It asserts:
 *   • each chunk() uploads IMMEDIATELY (before any close), in seq order, with bytes forwarded;
 *   • a simulated mid-meeting kill (close never called, no final) leaves every finished part
 *     ALREADY durable — no all-or-nothing loss (V2/#412);
 *   • the empty is_final chunk is FORWARDED (the COMPLETED signal), never dropped;
 *   • close() with no prior final sends EXACTLY ONE empty is_final fallback (the live Stop race),
 *     and fires at most once; a never-fed session's close is a no-op;
 *   • over the real RecordingService HTTP wire, session_uid == inv.connectionId on every chunk,
 *     seq monotone, only the last is_final (the split/404-guard contract).
 * Run: npx tsx src/recording.test.ts
 */
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { createBotRecordingSink, type ChunkUploader } from './recording.js';
import type { Invocation } from './config.js';
import type { RecordingMasterFormat } from '@vexa/recording';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const inv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'Vexa',
  redisUrl: 'redis://localhost:6379', recordingEnabled: true, connectionId: 'conn-xyz', ...over,
});

/** A record of one delivered chunk (what the injected uploader saw). */
interface Seen { seq: number; isFinal: boolean; format: string; len: number }
function fakeUploader(): { seen: Seen[]; upload: ChunkUploader } {
  const seen: Seen[] = [];
  const upload: ChunkUploader = (seq, isFinal, format, bytes) => {
    seen.push({ seq, isFinal, format, len: bytes.length });
  };
  return { seen, upload };
}
/** Drain the sink's internal upload queue (microtasks) — a macrotask tick runs after all of them. */
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

async function main(): Promise<void> {
  // ── 1) each timeslice uploads IMMEDIATELY, in seq order, bytes forwarded ─────────────────────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.chunk('google_meet/m1', 0, false, 'webm', new Uint8Array([1, 2, 3]));
    sink.chunk('google_meet/m1', 1, false, 'webm', new Uint8Array([4, 5]));
    sink.chunk('google_meet/m1', 2, false, 'webm', new Uint8Array([6]));
    await flush();
    check('per-chunk: 3 chunks → 3 immediate uploads BEFORE any close', seen.length === 3, String(seen.length));
    check('per-chunk: none accumulated as a whole-recording master', seen.every((s) => s.len <= 3), JSON.stringify(seen));
    check('per-chunk: all non-final', seen.every((s) => !s.isFinal), JSON.stringify(seen));
    check('per-chunk: seq order 0,1,2', seen.map((s) => s.seq).join(',') === '0,1,2', seen.map((s) => s.seq).join(','));
    check('per-chunk: bytes forwarded verbatim (3,2,1)', seen.map((s) => s.len).join(',') === '3,2,1', seen.map((s) => s.len).join(','));
  }

  // ── 2) kill-before-close: no close, no final → the N finished parts are ALREADY durable (#412) ──
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    for (let i = 0; i < 4; i++) sink.chunk('google_meet/m2', i, false, 'webm', new Uint8Array([i]));
    await flush();
    // simulate SIGKILL: close() is NEVER called — nothing was buffered waiting for it.
    check('crash: N=4 parts durable before any close (no all-or-nothing loss)', seen.length === 4, String(seen.length));
    check('crash: no final signal sent (recording stays in_progress; finalize-on-read still serves it)',
      seen.every((s) => !s.isFinal), JSON.stringify(seen));
  }

  // ── 3) the empty is_final chunk is FORWARDED (COMPLETED signal), not dropped ─────────────────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.chunk('google_meet/m3', 0, false, 'webm', new Uint8Array([9, 9]));
    sink.chunk('google_meet/m3', 1, true, 'webm', new Uint8Array(0)); // empty final = COMPLETED signal
    await flush();
    check('final: empty is_final forwarded (not dropped)', seen.length === 2, String(seen.length));
    const fin = seen[seen.length - 1];
    check('final: last upload is isFinal=true, 0 bytes', !!fin && fin.isFinal && fin.len === 0, JSON.stringify(fin));
  }

  // ── 4) close() with no prior final → EXACTLY ONE empty is_final fallback (live Stop race) ────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.chunk('google_meet/m4', 0, false, 'webm', new Uint8Array([1]));
    sink.chunk('google_meet/m4', 1, false, 'webm', new Uint8Array([2]));
    sink.close('google_meet/m4');   // host-side close (the WS dropped) — synthesize the final signal
    await flush();
    check('fallback: 2 data uploads + 1 synthesized final', seen.length === 3, String(seen.length));
    const fin = seen[2];
    check('fallback: final is isFinal=true, 0 bytes, seq after the last part (2)',
      !!fin && fin.isFinal && fin.len === 0 && fin.seq === 2, JSON.stringify(fin));
    sink.close('google_meet/m4');   // idempotent
    sink.chunk('google_meet/m4', 3, true, 'webm', new Uint8Array(0)); // late page callback
    await flush();
    check('fallback: fires at most once (second close and late page final are no-ops)',
      seen.filter((s) => s.isFinal).length === 1 && seen.length === 3, JSON.stringify(seen));
  }

  // ── 5) async data encode may finish after final → retain the late lower-seq data chunk ───────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.chunk('google_meet/m5', 0, false, 'webm', new Uint8Array([1]));
    sink.chunk('google_meet/m5', 2, true, 'webm', new Uint8Array(0));
    sink.chunk('google_meet/m5', 1, false, 'webm', new Uint8Array([2, 3])); // dataavailable encode completed late
    sink.chunk('google_meet/m5', 3, true, 'webm', new Uint8Array(0));       // duplicate final stays suppressed
    await flush();
    check('final-overtake: legitimate late data survives while duplicate final is suppressed',
      seen.map((s) => `${s.seq}:${s.isFinal ? 'final' : 'data'}`).join(',') === '0:data,2:final,1:data',
      JSON.stringify(seen));
  }

  // ── 6) close() AFTER a real final → no extra fallback (no double final) ──────────────────────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.chunk('google_meet/m6', 0, false, 'webm', new Uint8Array([1]));
    sink.chunk('google_meet/m6', 1, true, 'webm', new Uint8Array(0));
    sink.close('google_meet/m6');
    await flush();
    check('no-double-final: a real final was sent → close adds nothing',
      seen.filter((s) => s.isFinal).length === 1 && seen.length === 2, JSON.stringify(seen));
  }

  // ── 7) drain() waits for the serialized upload tail ───────────────────────────────────────────
  {
    let releaseUpload: () => void = () => {};
    const uploadTail = new Promise<void>((resolve) => { releaseUpload = resolve; });
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: async () => uploadTail });
    sink.chunk('google_meet/m7', 0, false, 'webm', new Uint8Array([1]));
    let drained = false;
    const drainP = sink.drain().then(() => { drained = true; });
    await flush();
    check('drain: remains pending while an accepted upload is pending', !drained);
    releaseUpload();
    await drainP;
    check('drain: resolves after the upload tail settles', drained);

    let releaseFinal: () => void = () => {};
    const finalTail = new Promise<void>((resolve) => { releaseFinal = resolve; });
    const fallback = createBotRecordingSink({
      inv: inv(),
      uploadChunk: async (_seq, isFinal) => { if (isFinal) await finalTail; },
    });
    fallback.chunk('google_meet/m7-fallback', 0, false, 'webm', new Uint8Array([1]));
    await fallback.drain();
    fallback.close('google_meet/m7-fallback');
    let fallbackDrained = false;
    const fallbackDrainP = fallback.drain().then(() => { fallbackDrained = true; });
    await flush();
    check('drain: fallback final remains part of the upload tail', !fallbackDrained);
    releaseFinal();
    await fallbackDrainP;
    check('drain: resolves after the fallback final upload settles', fallbackDrained);
  }

  // ── 8) close() on a never-fed session is a no-op (no phantom recording) ──────────────────────
  {
    const { seen, upload } = fakeUploader();
    const sink = createBotRecordingSink({ inv: inv(), uploadChunk: upload });
    sink.close('google_meet/never');
    await flush();
    check('empty session: close is a no-op (no upload)', seen.length === 0, String(seen.length));
  }

  // ── 9) the DEFAULT uploader on the real RecordingService HTTP wire: session_uid == connectionId ──
  {
    interface Wire { session_uid?: string; chunk_seq?: number; is_final?: boolean; format?: string; size?: number }
    const wire: Wire[] = [];
    const server = http.createServer((req, res) => {
      const parts: Buffer[] = [];
      req.on('data', (d: Buffer) => parts.push(d));
      req.on('end', () => {
        const text = Buffer.concat(parts).toString('latin1');
        const m = text.match(/name="metadata"[\s\S]*?\r\n\r\n(\{[\s\S]*?\})\r\n/);
        let meta: Record<string, unknown> = {};
        if (m) { try { meta = JSON.parse(m[1]); } catch { /* ignore */ } }
        wire.push({
          session_uid: meta.session_uid as string, chunk_seq: meta.chunk_seq as number,
          is_final: meta.is_final as boolean, format: meta.format as string,
          size: meta.file_size_bytes as number,
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok' }));
      });
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
    const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/internal/recordings/upload`;

    const sink = createBotRecordingSink({
      inv: inv({ connectionId: 'conn-xyz', meeting_id: 42, recordingUploadUrl: url, internalSecret: 's' }),
    });
    sink.chunk('google_meet/w', 0, false, 'webm', new Uint8Array([1, 2, 3, 4]));
    sink.chunk('google_meet/w', 1, false, 'webm', new Uint8Array([5, 6]));
    sink.chunk('google_meet/w', 2, true, 'webm', new Uint8Array(0));
    for (let i = 0; i < 100 && wire.length < 3; i++) await new Promise((r) => setTimeout(r, 10));
    await new Promise<void>((r) => server.close(() => r()));

    check('wire: 3 chunks POSTed to meeting-api', wire.length === 3, String(wire.length));
    check('wire: session_uid == inv.connectionId on EVERY chunk (never nativeMeetingId/master key)',
      wire.length === 3 && wire.every((w) => w.session_uid === 'conn-xyz'),
      JSON.stringify(wire.map((w) => w.session_uid)));
    check('wire: seq order 0,1,2', wire.map((w) => w.chunk_seq).join(',') === '0,1,2', wire.map((w) => w.chunk_seq).join(','));
    check('wire: only the LAST chunk is_final', wire.map((w) => w.is_final).join(',') === 'false,false,true',
      wire.map((w) => w.is_final).join(','));
  }

  if (failed) { console.error(`\n❌ recording (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ recording (L3): each recording.v1 timeslice uploads immediately (seq-ordered, session_uid==connectionId); a mid-meeting kill leaves every finished part durable; the empty is_final signal is forwarded; close() synthesizes the final signal at most once (injected uploader + real RecordingService HTTP wire · no whole-recording buffering).');
}

void main();
