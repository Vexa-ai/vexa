/**
 * A Teams speaker watcher that has gone blind must SAY SO.
 *
 * The voice-outline watcher's only output is a speaker transition, so a
 * `[data-tid="voice-level-stream-outline"]` selector that stops matching produces
 * perfect silence: no error, no warning, a clean bot log, a full transcript — and a
 * speaker column that is all provisional. This is exactly the #797 red. It cannot
 * know whether a silent room or a stale selector is the cause — but it can report
 * which of the two the DOM supports, mirroring Zoom's #852 blind report so a single
 * log grep (`NO ACTIVE SPEAKER seen`) catches both platforms.
 *
 *   tsx src/blindness.test.ts
 */
import { createTeamsSpeakers } from './msteams-speakers.js';

let checks = 0;
const ok = (cond: boolean, msg: string): void => {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
};

const g = globalThis as any;

// Minimal element/DOM shim — the blind report reads document.querySelector[All]
// only; it never observes (no tile carries the voice signal), so the RAF/observer
// machinery stays dormant and this shim can be tiny.
class FakeEl {
  querySelector(): FakeEl | null { return null; }
  querySelectorAll(): FakeEl[] { return []; }
  matches(): boolean { return false; }
}
g.HTMLElement = FakeEl;
g.MutationObserver = class { observe() {} disconnect() {} };
g.requestAnimationFrame = () => 0;
g.cancelAnimationFrame = () => {};

const VOICE = '[data-tid="voice-level-stream-outline"]';
function fakeDom(has: { voiceSignal: boolean; tiles: boolean }): void {
  const tile = new FakeEl();
  g.document = {
    body: new FakeEl(),
    querySelector: (sel: string): FakeEl | null => {
      if (sel === VOICE) return has.voiceSignal ? new FakeEl() : null;
      if (sel.includes('participant') || sel.includes('tile') || sel.includes('roster') || sel.includes('listitem'))
        return has.tiles ? tile : null;
      return null;
    },
    querySelectorAll: (sel: string): FakeEl[] => {
      if (sel === VOICE) return has.voiceSignal ? [new FakeEl(), new FakeEl()] : [];
      return has.tiles ? [tile] : [];
    },
  };
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  // ── voice-outline selector matches NOTHING and no tiles exist: DOM gone ───────
  {
    const logs: string[] = [];
    fakeDom({ voiceSignal: false, tiles: false });
    const w = createTeamsSpeakers({ onSpeaking: () => {}, blindReportMs: 40, log: (m) => logs.push(m) });
    await sleep(160);
    w.destroy();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.length > 0, 'a watcher that never sees a speaker reports it instead of staying silent');
    ok(blind.some((l) => l.includes('NONE of the participant containers exist')),
      'and names the cause the DOM supports: the participant selectors are stale');
  }

  // ── tiles present but the voice-outline signal is gone: the voice selector rotted ─
  {
    const logs: string[] = [];
    fakeDom({ voiceSignal: false, tiles: true });
    const w = createTeamsSpeakers({ onSpeaking: () => {}, blindReportMs: 40, log: (m) => logs.push(m) });
    await sleep(160);
    w.destroy();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.some((l) => l.includes('the voice selector is stale')),
      'tiles present but no voice-outline → the voice selector is named as stale, not the room quiet');
    ok(blind.some((l) => l.includes(VOICE)),
      'listing the voice selector that missed, so the fix does not need a source dive');
  }

  // ── voice-outline signal present but nobody named/lit: silent room or stale names ─
  {
    const logs: string[] = [];
    fakeDom({ voiceSignal: true, tiles: true });
    const w = createTeamsSpeakers({ onSpeaking: () => {}, blindReportMs: 40, log: (m) => logs.push(m) });
    await sleep(160);
    w.destroy();
    const blind = logs.filter((l) => l.includes('NO ACTIVE SPEAKER'));
    ok(blind.some((l) => l.includes('voice-outline signal present')),
      'signal present but unnamed is reported as an ambiguity (silent room OR stale names), not hidden');
  }

  console.log(`\n✅ teams blindness: ${checks} checks passed — a blind Teams watcher names what it cannot see.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
