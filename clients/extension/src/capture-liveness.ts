/**
 * Capture liveness — the P21 ("report state from evidence, not intent") rule for
 * the extension's capture state, as a PURE module so it is unit-testable without
 * chrome / WebSocket fakes (the extension's first state-machine tests).
 *
 * "Listening" must be EARNED by an observed audio frame — never asserted from the
 * Start command or the WS `ready`. The background worker applies these two
 * transitions to its session state:
 *
 *   connecting ─(WS ready)─► starting ─(first frame)─► capturing
 *                                │                          │
 *                                └──(no frame ≥ noSignalMs)─┴──► no-signal
 *   no-signal ─(a frame arrives)─► capturing            (self-heals)
 *
 * Before a frame is seen the state is `starting`, never `capturing` — so the panel
 * can never show "Listening — capturing N stream(s)" over silence. A stalled feed
 * flips visibly to `no-signal` with an actionable cause (P18 liveness, client-side).
 */
export type CaptureStatus = 'idle' | 'connecting' | 'starting' | 'capturing' | 'no-signal' | 'error';

/** A capture in any of these states is LIVE — don't double-start it, don't hot-reload over it. */
export const ACTIVE: ReadonlySet<CaptureStatus> = new Set<CaptureStatus>(['connecting', 'starting', 'capturing', 'no-signal']);
export const isActive = (s: CaptureStatus): boolean => ACTIVE.has(s);

/** P21 evidence rule: a real audio frame promotes a started/stalled capture to
 *  `capturing`. Idempotent once capturing; a no-op in idle/connecting/error (a
 *  stray frame must not resurrect a torn-down or never-started session). */
export function onFrameObserved(status: CaptureStatus): CaptureStatus {
  return (status === 'starting' || status === 'no-signal') ? 'capturing' : status;
}

/** P18 liveness, client-side: while a capture should be flowing, no frame within
 *  `noSignalMs` flips it to `no-signal` with an actionable cause. `lastFrameAt === 0`
 *  means no frame yet, so the warm-up grace is measured from `startedAt`. Returns the
 *  new status + hint, or `null` when nothing should change. */
export function noSignalCheck(a: {
  status: CaptureStatus; paused: boolean; frames: number;
  lastFrameAt: number; startedAt: number; now: number; noSignalMs: number; mixed: boolean;
}): { status: CaptureStatus; error: string } | null {
  if (a.paused) return null;                                   // suspended on purpose — silence is expected
  if (a.status !== 'starting' && a.status !== 'capturing') return null;
  const since = a.now - (a.lastFrameAt || a.startedAt);
  if (since <= a.noSignalMs) return null;                      // still within grace / recently fed
  const error = a.frames === 0
    ? (a.mixed
        ? 'no audio — capturing 0 streams. Click the Vexa toolbar icon ON this tab to mint capture (it is lost on reload).'
        : 'no audio — capturing 0 streams. Check the tab is in the meeting and someone is speaking.')
    : 'audio stalled — the capture feed stopped flowing';
  return { status: 'no-signal', error };
}
