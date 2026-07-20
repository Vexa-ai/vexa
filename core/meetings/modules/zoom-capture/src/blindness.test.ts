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
import { createZoomSpeakers } from './zoom-speakers.js';

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
    fakeDom({ hasContainer: false });
    const w = createZoomSpeakers({ pollMs: 5, blindReportMs: 40, log: (m) => logs.push(m) });
    await sleep(160);
    w.destroy?.();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.length > 0, 'a watcher that never sees a speaker reports it instead of staying silent');
    ok(blind.some((l) => l.includes('NONE of the containers exist')),
      'and names the cause the DOM supports: the selectors are stale, not the room quiet');
    ok(blind.some((l) => l.includes('speaker-active-container')),
      'listing the selectors that missed, so the fix does not need a source dive');
  }

  // ── containers exist but nobody is lit: a genuinely quiet room ─────────────
  {
    const logs: string[] = [];
    fakeDom({ hasContainer: true });
    const w = createZoomSpeakers({ pollMs: 5, blindReportMs: 40, log: (m) => logs.push(m) });
    await sleep(160);
    w.destroy?.();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.length > 0, 'a quiet room is reported too — the watcher does not guess which it is');
    ok(blind.some((l) => l.includes('the room may be silent')),
      'but it is reported as a SILENT ROOM, not as stale selectors — the DOM distinguishes them');
  }

  console.log(`\n✅ blindness: ${checks} checks passed — a blind watcher names what it cannot see.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
