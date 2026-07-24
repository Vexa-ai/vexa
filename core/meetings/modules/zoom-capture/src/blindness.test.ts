/**
 * A speaker watcher that has gone blind must SAY SO.
 *
 * This watcher's only output is a speaker transition, so selectors that stop matching produce
 * perfect silence: no error, no warning, a clean bot log, a full transcript — and a speaker column
 * reading `seg_0, seg_4, seg_7`. Observed live on 2026-07-20 (#852): Zoom's web client renders none
 * of ACTIVE_CONTAINER_SELECTORS any more, every 250ms poll returned null for an entire meeting,
 * `hint-counters` read all zeros, and nothing anywhere reported it. All-zero counters are
 * indistinguishable from a meeting where nobody spoke.
 *
 * The watcher cannot know whether a silent room or a stale selector is the cause — but it can
 * report which of the two the DOM supports, which is the difference between a defect found in the
 * first minute and one found by reading a bad transcript.
 *
 *   tsx src/blindness.test.ts
 */
import {
  createZoomSpeakers,
  type ZoomNameUnresolvedObservation,
} from './zoom-speakers.js';

let checks = 0;
const ok = (cond: boolean, msg: string): void => {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
};

const g = globalThis as any;
function fakeDom(html: { hasContainer: boolean }): void {
  g.document = {
    querySelector: (sel: string) =>
      html.hasContainer && sel.includes('speaker-active-container') ? { querySelector: () => null, textContent: '' } : null,
    querySelectorAll: () => [],
  };
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  // ── selectors match NOTHING: the live failure ──────────────────────────────
  {
    const logs: string[] = [];
    const unresolved: ZoomNameUnresolvedObservation[] = [];
    fakeDom({ hasContainer: false });
    const w = createZoomSpeakers({
      pollMs: 5,
      blindReportMs: 40,
      log: (m) => logs.push(m),
      onNameUnresolved: (observation) => unresolved.push(observation),
    });
    await sleep(160);
    w.destroy?.();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.length > 0, 'a watcher that never sees a speaker reports it instead of staying silent');
    ok(blind.some((l) => l.includes('none of') && l.includes('exist here')),
      'and names the cause the DOM supports: the selectors are stale, not the room quiet');
    ok(blind.some((l) => l.includes('speaker-active-container')),
      'listing the selectors that missed, so the fix does not need a source dive');
    ok(unresolved.length === 0,
      'no active container is not fabricated into an unresolved identity');
  }

  // ── an ACTIVE container exists but its footer is absent ────────────────────
  {
    const logs: string[] = [];
    const unresolved: ZoomNameUnresolvedObservation[] = [];
    fakeDom({ hasContainer: true });
    const w = createZoomSpeakers({
      pollMs: 5,
      blindReportMs: 40,
      log: (m) => logs.push(m),
      onNameUnresolved: (observation) => unresolved.push(observation),
    });
    await sleep(160);
    w.destroy?.();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(unresolved.length === 1 && unresolved[0]?.reason === 'footer-absent',
      'an active container without a footer produces one typed footer-absent observation');
    ok(blind.some((l) => l.includes('active container present') && l.includes('display name unresolved')),
      'the periodic report describes the observed identity failure, not a quiet room');
  }

  console.log(`\n✅ blindness: ${checks} checks passed — a blind watcher names what it cannot see.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
