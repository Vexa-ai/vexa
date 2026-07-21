/**
 * A Teams name the hashed-class selectors CANNOT select is still recovered — structurally.
 *
 * The #853 live witness (issue comment, 2026-07-21): the bot IS served participant names in
 * the DOM (unlike Zoom #852 — Teams paints a name pill in every tile), but every
 * teamsNameSelector missed. The lead selector `div[class*="___2u340f0"]` is an extension-era
 * minified hash that has since ROTATED; today's live name div carries only atomic hashes
 * (`___12zni01 f1cmbuwj fv6wr3j fz5stix`) inside a container `___1504rl1 f1euv43f ftuwxu6`,
 * matching NONE of the explicit selectors — no data-tid, no aria name. extractName returned '',
 * and the no-name guard discarded every voice-outline transition: perfect on-screen names, an
 * anonymous transcript. That is the #797 red.
 *
 * The fixture below is RECONSTRUCTED to mirror the live-witnessed class STRUCTURE
 * (`___1504rl1` container / `___12zni01` text leaf, atomic hashes only, no name attrs) — it is
 * NOT a captured byte-for-byte DOM dump of the live meeting. What it faithfully reproduces is
 * the property that broke the resolver: a name node reachable by traversal but by no selector.
 *
 *   red  = explicit selectors only (structuralFallback: false) → ''    (the #797/#853 red)
 *   green = with the structural fallback                       → "Dmitry Grankin"
 * plus negative controls: a timer / UI-control-only tile must stay '' (no false name).
 *
 *   tsx src/name-drift.test.ts
 */
import { extractTeamsSpeakerName } from './msteams-speakers.js';

let checks = 0;
const ok = (cond: boolean, msg: string): void => {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
};

// ── Minimal honest DOM shim ───────────────────────────────────────────────────────────────
// Zero-dependency module (browser-bundled standalone — no jsdom permitted, see package.json),
// so we hand-roll only what extractTeamsSpeakerName touches: querySelector over the tree (it
// must GENUINELY evaluate the explicit selectors so their miss is real, not stubbed),
// querySelectorAll('*') for the fallback's leaf scan, children/textContent/getAttribute.
class El {
  tag: string;
  classAttr: string;
  attrs: Record<string, string>;
  text: string;
  kids: El[];
  constructor(tag: string, opts: { class?: string; attrs?: Record<string, string>; text?: string } = {}) {
    this.tag = tag.toLowerCase();
    this.classAttr = opts.class || '';
    this.attrs = opts.attrs || {};
    this.text = opts.text || '';
    this.kids = [];
  }
  add(...children: El[]): El { this.kids.push(...children); return this; }
  get children(): El[] { return this.kids; }
  get textContent(): string { return this.kids.length ? this.kids.map((k) => k.textContent).join('') : this.text; }
  getAttribute(name: string): string | null {
    if (name === 'class') return this.classAttr || null;
    return name in this.attrs ? this.attrs[name] : null;
  }
  private descendants(): El[] {
    const out: El[] = [];
    const walk = (n: El): void => { for (const k of n.kids) { out.push(k); walk(k); } };
    walk(this);
    return out;
  }
  // Supports exactly the selector shapes in teamsNameSelectors: `tag[class*="x"]`,
  // `[attr*="x"]`, `[attr]`, `tag[attr]`, `.class`.
  private matchesSel(sel: string): boolean {
    const m = sel.match(/^([a-z0-9]*)(?:\[([a-z-]+)(?:\*?=)?(?:"([^"]*)")?\]|\.([\w-]+))?$/i);
    if (!m) return false;
    const [, tag, attr, attrVal, cls] = m;
    if (tag && this.tag !== tag.toLowerCase()) return false;
    if (cls) return this.classAttr.split(/\s+/).includes(cls) || this.classAttr.includes(cls);
    if (attr) {
      const v = this.getAttribute(attr);
      if (v == null) return false;
      return attrVal ? v.includes(attrVal) : true;
    }
    return !!tag; // bare tag selector
  }
  querySelector(sel: string): El | null {
    for (const d of this.descendants()) if (d.matchesSel(sel)) return d;
    return null;
  }
  querySelectorAll(sel: string): El[] {
    if (sel === '*') return this.descendants();
    return this.descendants().filter((d) => d.matchesSel(sel));
  }
}

// ── Fixture: the RECONSTRUCTED live-witnessed tile ────────────────────────────────────────
// Container `___1504rl1 …` → name leaf `___12zni01 …` (atomic hashes only, no name attrs).
// This is the shape #853 witnessed live; it is not a captured DOM dump.
function witnessedTile(name: string): El {
  const tile = new El('div', { class: 'fui-Primitive ___1r8x2k0', attrs: { 'data-tid': 'video-tile' } });
  const nameContainer = new El('div', { class: '___1504rl1 f1euv43f ftuwxu6' });
  const nameLeaf = new El('div', { class: '___12zni01 f1cmbuwj fv6wr3j fz5stix', text: name });
  nameContainer.add(nameLeaf);
  tile.add(nameContainer);
  return tile;
}

// A tile whose only text-bearing leaves are a call timer + a UI control word — never a name.
function noNameTile(): El {
  const tile = new El('div', { class: 'fui-Primitive ___1r8x2k0', attrs: { 'data-tid': 'video-tile' } });
  const timer = new El('span', { class: '___abc123', text: '05:14' });          // leading clock
  const control = new El('span', { class: '___def456', text: 'Microphone' });    // forbidden UI word
  tile.add(timer, control);
  return tile;
}

function main(): void {
  const tile = witnessedTile('Dmitry Grankin');

  // RED: the explicit selector set (incl. the rotated lead `div[class*="___2u340f0"]`) matches
  // nothing on the reconstructed tile — this is the #797/#853 red, reproduced offline.
  ok(extractTeamsSpeakerName(tile, { structuralFallback: false }) === '',
    'RED: with explicit selectors only, the hashed-class name node resolves to \'\' — the #797/#853 red');

  // Prove the red is caused by the DRIFT, not a broken shim: a legacy tile whose name node DOES
  // carry the (rotated-in) lead class still resolves via the fast path with the fallback off.
  const legacyTile = new El('div').add(new El('div', { class: '___2u340f0 legacy', text: 'Legacy Name' }));
  ok(extractTeamsSpeakerName(legacyTile, { structuralFallback: false }) === 'Legacy Name',
    'control: a name node matching an explicit selector still wins via the fast path (shim selectors are real)');

  // GREEN: the structural fallback recovers the name a human plainly reads in the tile.
  ok(extractTeamsSpeakerName(tile) === 'Dmitry Grankin',
    'GREEN: the structural fallback recovers "Dmitry Grankin" from the atomic-hash leaf');

  // The fast path still wins when it can — the fallback never overrides a real selector hit.
  ok(extractTeamsSpeakerName(legacyTile) === 'Legacy Name',
    'fast path precedence: an explicit-selector name is preferred over the structural scan');

  // Negative controls: the fallback must NOT invent a name from timers / UI-control words.
  ok(extractTeamsSpeakerName(noNameTile()) === '',
    'guard: a timer (05:14) + control-word (Microphone) tile yields \'\' — no false name from chrome');
  ok(extractTeamsSpeakerName(new El('div').add(new El('span', { text: '00:30' }))) === '',
    'guard: a lone call-clock leaf is rejected by the timer regex, not returned as a name');

  console.log(`\n✅ teams name-drift: ${checks} checks passed — a name the resolver cannot select is recovered structurally.`);
}

main();
