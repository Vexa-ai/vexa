/**
 * The roster panel — the second DOM surface, and the m37 counter-example.
 *
 * Every name path in this package rode the participant TILES until meeting 37 showed what that
 * costs: the page degraded to ONE tile with no voice outline, the tile-based roster walk resolved
 * nothing, and a meeting whose only participant was sitting named in the roster shipped as
 * "Speaker A". The panel is a different subtree with a different lifecycle; it survives the layout
 * that drops tiles.
 *
 * The test also pins what the panel reader must NOT do. It never opens the panel — clicking the
 * roster button changes what the humans in the meeting see, and the ruling that is retiring the
 * captions lane applies to this too: the bot reads the page, it does not operate it. A closed panel
 * yields nothing, and `name-sources-absent` is how that gets said out loud instead of arriving as
 * an anonymous transcript nobody can explain afterwards.
 *
 * Run: npx tsx src/roster-panel.test.ts
 */
import {
  readTeamsRosterPanel, readTeamsRosterPanelState, isTeamsDisplayNameCandidate,
} from './msteams-speakers.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

/** A minimal DOM good enough for querySelectorAll + the leaf scan the resolver ends in. */
function makeDom(html: { panel?: string[]; panelSelector?: string; entrySelector?: string } = {}): ParentNode {
  const names = html.panel ?? [];
  const panelSel = html.panelSelector ?? 'data-tid="roster"';
  const entrySel = html.entrySelector ?? 'role="treeitem"';
  const leaf = (text: string) => ({
    childElementCount: 0, textContent: text, children: [],
    getAttribute: () => null, querySelector: () => null, querySelectorAll: () => [],
  });
  const entries = names.map((n) => {
    const kids = [leaf(n)];
    return {
      childElementCount: 1, textContent: n, children: kids,
      getAttribute: (a: string) => (a === 'aria-label' ? `${n}, participant` : null),
      querySelector: () => null,
      querySelectorAll: (sel: string) => (sel === '*' ? kids : []),
      closest: () => null,
    };
  });
  const panel = {
    querySelectorAll: (sel: string) => (sel.includes(entrySel.split('=')[1].replace(/"/g, '')) || sel === '[role="treeitem"]' ? entries : []),
  };
  return {
    querySelectorAll: (sel: string) => (sel.includes(panelSel.split('=')[1].replace(/"/g, '')) || sel === '[data-tid="roster"]' ? [panel] : []),
  } as unknown as ParentNode;
}

// Give the leaf scan the globals it expects (HTMLElement instanceof + a document for the default arg).
(globalThis as any).HTMLElement = class { };
Object.setPrototypeOf(makeDom, Object.prototype);

{
  // The m37 shape, with the panel present: tiles are useless, the panel is not.
  const dom = makeDom({ panel: ['Dmitry Grankin'] });
  // The entries in this stub are plain objects, so instanceof HTMLElement must not gate them out.
  (globalThis as any).HTMLElement = Object;
  const names = readTeamsRosterPanel(dom);
  check('the panel yields the participant the tiles could not name',
    names.includes('Dmitry Grankin'), JSON.stringify(names));
  check('and yields each participant once, however many selectors matched',
    names.length === new Set(names).size, JSON.stringify(names));
}
{
  const dom = makeDom({ panel: ['Dmitry Grankin', 'mic_off'] });
  (globalThis as any).HTMLElement = Object;
  const state = readTeamsRosterPanelState(dom);
  check('the panel accounts for an unresolved row instead of dropping it from completeness',
    state.names.length === 1 && state.names[0] === 'Dmitry Grankin'
      && state.entries.length === 2 && state.entries[1] === null,
    JSON.stringify(state));
}
{
  // A CLOSED panel is the m37 state. It must return nothing — and must not throw, because the scan
  // that calls it runs on every heartbeat of every meeting.
  const empty = { querySelectorAll: () => [] } as unknown as ParentNode;
  let threw = false;
  let names: string[] = [];
  try { names = readTeamsRosterPanel(empty); } catch { threw = true; }
  check('a closed panel yields nothing and never throws', !threw && names.length === 0, JSON.stringify(names));
}
{
  // The panel is not a way around the one guard: it feeds the same name stream, so the bot and the
  // platform's placeholders must be refused there exactly as they are on a tile.
  check('a placeholder in the roster is still not a name', !isTeamsDisplayNameCandidate('Unknown user'));
  check('a control label in the roster is still not a name', !isTeamsDisplayNameCandidate('mic_off'));
  check('a real participant still is', isTeamsDisplayNameCandidate('Dmitry Grankin'));
}

if (failed) { console.error(`\n❌ roster-panel: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ roster-panel: the panel names participants the tiles cannot, a closed panel is silent rather than fatal, and it is not a bypass around the name guard.');
