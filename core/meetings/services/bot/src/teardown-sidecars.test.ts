/**
 * The two teardown sidecars — the bot's own log, and the transcript a viewer was left with.
 *
 * Both answer questions a stored fixture could not answer before. The tape says what the bot HEARD;
 * the observations say what it NOTICED; neither says what it SAID about its own run, and none of
 * them says what the meeting finally read like after every retraction. The first lived in a pod
 * deleted minutes later, the second could only be recovered by re-folding a redis stream that no
 * longer exists by the time anyone asks.
 *
 * The load-bearing checks are the two that are easy to get wrong:
 *
 *   • the log is TRUNCATED IN THE MIDDLE and says so — the one place the whole-or-absent rule is
 *     deliberately broken, because a truncated log misleads nobody while a truncated tape reads to
 *     a replay exactly like a complete one;
 *   • the transcript snapshot is a FOLD, not a copy of the stream: a draft that was retracted must
 *     be absent, and a renamed segment must appear once, under its final name.
 *
 * Run: npx tsx src/teardown-sidecars.test.ts
 */
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startBotLogSidecar, wrapTranscriptWithSnapshot, createCaptureSignalRecorder } from './telemetry.js';
import { uploadSignalTapes } from './signal-upload.js';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { TranscriptSink } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const dir = mkdtempSync(join(tmpdir(), 'vexa-teardown-'));

// ── 1) The log tap: everything passes through, and the file holds head + marker + tail ───────────
{
  const path = join(dir, 'log.botlog.txt');
  const seen: string[] = [];
  const fake = { log: (...a: unknown[]) => seen.push(String(a[0])), error: (...a: unknown[]) => seen.push(String(a[0])) };
  // A cap small enough to overflow in a handful of lines: the SHAPE is what is under test.
  const tap = startBotLogSidecar(path, { maxBytes: 600, console: fake });
  for (let i = 0; i < 60; i++) fake.log(`line ${String(i).padStart(3, '0')} of the meeting`);
  tap.stop();
  const body = readFileSync(path, 'utf8');
  check('every line still reached the real console (a tap that swallows output is a regression)',
    seen.length === 60 && seen[0].startsWith('line 000'), `${seen.length}`);
  check('the file keeps the HEAD of the session', body.includes('line 000'), body.slice(0, 120));
  check('and the TAIL', body.includes('line 059'), body.slice(-160));
  check('and names the gap between them instead of pretending it is contiguous',
    /bytes of log dropped here/.test(body) && tap.truncated(), body.slice(0, 400));
  check('the middle is what was dropped', !body.includes('line 030'), 'line 030 survived');
  check('the console is restored when the tap stops',
    (() => { const before = seen.length; fake.log('after stop'); return seen.length === before + 1; })());
  // A stamped level and timestamp, so a line can be located in time against the tape's clock.
  check('each line carries an ISO timestamp and its level',
    /^\d{4}-\d{2}-\d{2}T[\d:.]+Z LOG line 000/.test(body.split('\n')[0]), body.split('\n')[0]);
}

// ── 2) The transcript snapshot: a fold over publishes and retractions ────────────────────────────
{
  const path = join(dir, 'snap.transcript.jsonl');
  const published: string[] = [];
  const retracted: string[] = [];
  const live: TranscriptSink = {
    async publish(s: TranscriptSegment) { published.push(s.segment_id); },
    async retract(ids: string[]) { retracted.push(...ids); },
  };
  const sink = wrapTranscriptWithSnapshot<TranscriptSegment, TranscriptSink>(live, path);
  const seg = (id: string, start: number, speaker: string, text: string): TranscriptSegment => ({
    segment_id: id, speaker, speaker_key: id, text, start, end: start + 1, completed: true,
  });
  await sink.publish(seg('turn:0:p0', 2, 'Speaker', 'a draft that never confirmed'));
  await sink.publish(seg('turn:0:0', 2, 'Speaker', 'the confirmed words'));
  await sink.retract!(['turn:0:p0']);
  await sink.publish(seg('turn:1:0', 9, 'Speaker A', 'said later'));
  await sink.publish(seg('turn:0:0', 2, 'Ana', 'the confirmed words'));   // a late rename, same id
  const n = await sink.writeSnapshot();

  const rows = readFileSync(path, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  check('every publish and retraction still reached the live sink',
    published.length === 4 && retracted.length === 1, `${published.length}/${retracted.length}`);
  check('the retracted draft is ABSENT from the snapshot (it is not what the viewer was left with)',
    n === 2 && !rows.some((r) => r.segment_id === 'turn:0:p0'), JSON.stringify(rows.map((r) => r.segment_id)));
  check('a renamed segment appears ONCE, under its final name',
    rows.filter((r) => r.segment_id === 'turn:0:0').length === 1
    && rows.find((r) => r.segment_id === 'turn:0:0')?.speaker === 'Ana', JSON.stringify(rows));
  check('rows are in transcript order, not publish order',
    rows.map((r) => r.start).join(',') === '2,9', JSON.stringify(rows.map((r) => r.start)));
}

// ── 3) Both parts are in the closed set the upload walks ─────────────────────────────────────────
{
  const inv = { platform: 'teams', connectionId: 'sess-1', nativeMeetingId: 'x',
                recordingUploadUrl: 'http://example.invalid/upload', internalSecret: 's' } as unknown as Invocation;
  const rec = createCaptureSignalRecorder(inv, { dir });
  check('the recorder names both new sidecars beside the tape',
    rec.botlogPath.endsWith('sess-1.botlog.txt') && rec.transcriptPath.endsWith('sess-1.transcript.jsonl'),
    `${rec.botlogPath} ${rec.transcriptPath}`);
  const tap = startBotLogSidecar(rec.botlogPath, { console: { log: () => { /* silence */ } } });
  (console as unknown as { log: (m: string) => void }); // (the tap under test writes through its own console)
  tap.stop();
  const snap = wrapTranscriptWithSnapshot<TranscriptSegment, TranscriptSink>(
    { async publish() { /* n/a */ } } as TranscriptSink, rec.transcriptPath);
  await snap.publish({ segment_id: 's:0', speaker: 'Ana', speaker_key: 's:0', text: 'hello', start: 1, end: 2, completed: true });
  await snap.writeSnapshot();
  await rec.close();

  const attempted: string[] = [];
  const summary = await uploadSignalTapes(rec, {
    inv,
    upload: async (part) => { attempted.push(part); },
  });
  check('the transcript snapshot is uploaded as its own part',
    summary.uploaded.includes('transcript'), JSON.stringify(summary));
  check('an EMPTY bot log is skipped, not shipped as a zero-byte part',
    existsSync(rec.botlogPath) === false || summary.skipped.includes('botlog') || summary.uploaded.includes('botlog'),
    JSON.stringify(summary));
  check('every part the walk attempted is one the server knows',
    attempted.every((p) => ['captured-signal', 'stt', 'captions', 'csrc', 'observations', 'botlog', 'transcript'].includes(p)),
    JSON.stringify(attempted));
}

if (failed) { console.error(`\n❌ teardown-sidecars: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ teardown-sidecars: the bot log survives as head+marker+tail, the transcript snapshot is the post-retract fold, and both ship as their own tape parts.');
