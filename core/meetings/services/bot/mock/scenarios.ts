/**
 * The MOCK BOT scenario library (Lane A · A:V0) — a contract-faithful stand-in for the real bot
 * that lets the BACKEND be validated in isolation (SoC: backend ⊥ worker), at L3, on the real
 * compose stack, with NO browser/STT/GPU. It reuses the bot's REAL `createOrchestrator` + the four
 * REAL adapters (lifecycle-http · transcript-redis · acts-redis · the recording uploader); only the
 * two heavy ports are faked here:
 *
 *   JoinDriver → `fakeJoinDriver`  (returns a scenario verdict instead of driving a browser)
 *   Pipeline   → `fakePipeline`    (publishes scenario transcript.v1 segments instead of capturing)
 *
 * Because the orchestrator + adapters are the real ones, the backend sees prod-identical lifecycle.v1
 * emission, and the mock CANNOT emit off-contract (P5/P16, ARCH §5). A scenario drives one backend
 * behaviour: normal · join-timeout · reject · crash · immediate-stop · continue · speak-ack ·
 * emit-n-segments · slow-join · recording · silence-left-alone. Selected by env `MOCK_SCENARIO`.
 */
import type { JoinDriver, JoinOutcome, Pipeline, TranscriptSink } from '../src/ports.js';
import type { CompletionReason, TranscriptSegment } from '../src/contracts.js';
import type { Invocation } from '../src/config.js';

export type ScenarioName =
  | 'normal' | 'join-timeout' | 'reject' | 'crash' | 'immediate-stop'
  | 'continue' | 'speak-ack' | 'emit-n-segments' | 'slow-join' | 'recording'
  | 'silence-left-alone';

export interface Scenario {
  name: ScenarioName;
  /** the join+admission verdict the FakeJoinDriver returns. */
  join: JoinOutcome;
  /** how long the driver stalls in awaiting_admission before resolving (slow-join). */
  joinDelayMs?: number;
  /** transcript.v1 segments published during the active phase. */
  segments?: number;
  /** pipeline.start throws → the orchestrator drives to failed(stage=active) (crash). */
  crashOnStart?: boolean;
  /** upload one recording chunk to the backend during the active phase (recording leg). */
  recording?: boolean;
  /** self-end the active phase after N ms → completed(stopped). undefined ⇒ wait for the backend
   *  (an acts.v1 `leave` on DELETE /bots, or SIGTERM) — the immediate-stop path. */
  endAfterMs?: number;
  /** Use the real silence AlonenessSource instead of a no-op source. */
  silenceAlone?: boolean;
}

/** The scenario registry. Live timings; the fidelity test overrides them to run in milliseconds. */
export const SCENARIOS: Record<ScenarioName, Scenario> = {
  'normal':          { name: 'normal',          join: 'admitted', segments: 3, recording: true, endAfterMs: 1500 },
  'emit-n-segments': { name: 'emit-n-segments', join: 'admitted', segments: 12,                 endAfterMs: 1500 },
  'slow-join':       { name: 'slow-join',       join: 'admitted', segments: 1, joinDelayMs: 1500, endAfterMs: 1000 },
  'recording':       { name: 'recording',       join: 'admitted', segments: 1, recording: true, endAfterMs: 1000 },
  'speak-ack':       { name: 'speak-ack',       join: 'admitted', segments: 1,                  endAfterMs: 2500 },
  'continue':        { name: 'continue',        join: 'admitted', segments: 2, recording: true, endAfterMs: 1200 },
  'immediate-stop':  { name: 'immediate-stop',  join: 'admitted' /* no endAfterMs → backend drives the stop */ },
  'join-timeout':    { name: 'join-timeout',    join: 'timeout' },
  'reject':          { name: 'reject',          join: 'rejected' },
  'crash':           { name: 'crash',           join: 'admitted', crashOnStart: true },
  'silence-left-alone': { name: 'silence-left-alone', join: 'admitted', silenceAlone: true },
};

export function getScenario(name: string | undefined): Scenario {
  const s = SCENARIOS[(name ?? 'normal') as ScenarioName];
  if (!s) throw new Error(`mock: unknown MOCK_SCENARIO "${name}" — one of: ${Object.keys(SCENARIOS).join(', ')}`);
  return s;
}

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** A transcript.v1-valid segment (the shape the real gmeet/mixed pipeline emits per utterance). */
export function mockSegment(inv: Invocation, i: number, text?: string): TranscriptSegment {
  return {
    segment_id: `${inv.connectionId ?? inv.nativeMeetingId ?? 'sess'}:mock:${i}`,
    speaker: i % 2 === 0 ? 'Speaker A' : 'Speaker B',
    text: text ?? `mock utterance ${i}`,
    start: i * 1.0,
    end: i * 1.0 + 0.8,
    completed: true,
    source: 'glow-bound',
  };
}

export interface JoinOpts { onRemovalRef?: (fire: () => void) => void; joinDelayMs?: number; }

/** FakeJoinDriver — reports awaiting_admission, optionally stalls, then returns the scenario verdict
 *  (admitted → also reports active). The real orchestrator turns this into the lifecycle.v1 sequence. */
export function fakeJoinDriver(sc: Scenario, opts: JoinOpts = {}): JoinDriver {
  const stall = opts.joinDelayMs ?? sc.joinDelayMs ?? 0;
  return {
    async join(report) {
      await report('awaiting_admission');
      if (stall) await delay(stall);
      if (sc.join === 'admitted') { await report('active'); return 'admitted'; }
      return sc.join;
    },
    onRemoval(cb) { opts.onRemovalRef?.(cb); return () => { /* */ }; },
    async leave() { /* best-effort no-op */ },
  };
}

export interface PipelineOpts {
  /** wired to the orchestrator's `stop` at the composition root, so a self-ending scenario completes. */
  endRun?: (reason: CompletionReason) => void;
  /** wired to the recording uploader (main.ts) — POSTs a chunk to the backend. */
  recordChunk?: () => Promise<void>;
  segGapMs?: number;
  endAfterMs?: number;        // overrides sc.endAfterMs (the fidelity test runs fast)
  log?: (m: string) => void;
}

/** FakePipeline — on start() (post-admission), publishes the scenario's transcript.v1 segments through
 *  the REAL transcript sink, optionally uploads a recording chunk, then self-ends (→ stopped) unless the
 *  scenario waits for the backend. `crashOnStart` throws → the orchestrator emits failed(stage=active). */
export function fakePipeline(sc: Scenario, inv: Invocation, transcript: TranscriptSink, opts: PipelineOpts = {}): Pipeline {
  const gap = opts.segGapMs ?? 50;
  const endAfter = opts.endAfterMs ?? sc.endAfterMs;
  let cancelled = false;
  return {
    async start() {
      if (sc.crashOnStart) throw new Error('mock: simulated capture-init failure (crash scenario)');
      // Run the active-phase work AFTER start() returns (the orchestrator subscribes acts next).
      void (async () => {
        const n = sc.segments ?? 0;
        for (let i = 0; i < n && !cancelled; i++) {
          await transcript.publish(mockSegment(inv, i)).catch((e) => opts.log?.(`mock: publish failed: ${String(e)}`));
          if (gap) await delay(gap);
        }
        if (sc.recording && opts.recordChunk) await opts.recordChunk().catch((e) => opts.log?.(`mock: recordChunk failed: ${String(e)}`));
        if (endAfter != null) { await delay(endAfter); if (!cancelled) opts.endRun?.('stopped'); }
      })();
    },
    async stop() { cancelled = true; },
  };
}
