/**
 * P18 fail-loud GATE (failure injection). The 402-silent-drop class of bug: when the STT
 * throws, the pipeline used to swallow it in a bare `catch {}` and emit nothing — so "out
 * of balance" looked identical to "no speech". This pins the fix: a transcribe fault MUST
 * be SURFACED (onError) and ATTRIBUTED (a typed TranscriptionError), while the pipeline
 * still degrades gracefully (no crash, no phantom segment).
 * Run: npm test (chained)  or  npx tsx src/fault-surfacing.test.ts
 */
import {
  createAlloySttTelemetryTracker,
  createGmeetPipeline,
  type TranscriptSegment,
  type TranscriptSink,
} from './index.js';
import { TranscriptionError } from '@vexa/transcribe-whisper';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

async function run() {
  const faults: unknown[] = [];
  const segments: TranscriptSegment[] = [];
  const rawDetail = 'STT response included Bearer sk-alloy-secret-must-not-render';
  const tracker = createAlloySttTelemetryTracker({
    meetingId: 'fault-surfacing',
    nativeMeetingId: 'alloy-fault-surfacing',
  });
  // The exact live incident: the STT is out of balance → a non-retryable 402.
  const transcribe = async (): Promise<never> => {
    throw new TranscriptionError('payment_required', 402, rawDetail, false);
  };
  const sink: TranscriptSink = { segment: (s) => segments.push(s), draft: () => {}, finalize: () => {} };
  const pipe = createGmeetPipeline({
    transcribe,
    sink,
    onError: (e) => faults.push(e),
    alloySttTelemetry: tracker,
  });

  const ONE_SEC = new Float32Array(16000).fill(0.1);
  pipe.feedAudio(0, 'Alice', ONE_SEC, 0);
  await pipe.flush();
  await pipe.dispose();

  check('the STT fault was SURFACED via onError (not swallowed)', faults.length >= 1, `got ${faults.length}`);
  const f = faults[0] as TranscriptionError;
  check('the fault is the typed TranscriptionError', f instanceof TranscriptionError);
  check('attributed: source=stt, kind=payment_required',
    f?.source === 'stt' && f?.kind === 'payment_required', JSON.stringify({ source: f?.source, kind: f?.kind }));
  check('the server-side onError receives the original raw STT detail',
    f?.detail === rawDetail, `got ${JSON.stringify(f?.detail)}`);
  check('non-retryable (a 402 must not be retried forever)', f?.retryable === false);
  check('graceful degrade: no phantom CONFIRMED segment on failure', segments.length === 0, `got ${segments.length}`);
  const telemetryError = tracker.snapshot().last_error;
  check('telemetry attributes the STT failure without persisting raw detail',
    telemetryError?.code === 'stt_failed'
      && telemetryError.message === 'STT payment required (HTTP 402)'
      && !telemetryError.message.includes('Bearer sk-alloy-secret-must-not-render')
      && !telemetryError.message.includes(rawDetail), JSON.stringify(telemetryError));

  if (failed) { console.error(`\n❌ fault-surfacing: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ fault-surfacing (P18): an STT fault is surfaced + attributed via onError; the pipeline degrades without a phantom segment.');
}
run().catch((e) => { console.error(e); process.exit(1); });
