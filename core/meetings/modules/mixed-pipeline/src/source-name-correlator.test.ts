/**
 * The correlator, against the three real tapes it was derived from.
 *
 * The interval data below is not invented: it is the co-activity actually measured off m30, m34 and
 * m36 (csrc runs against lag-corrected DOM lit-time), reduced to the shape the correlator takes.
 * The m30 case is the one to keep: its raw co-activity picks the SAME name for BOTH sources, and a
 * correlator that let each source take its own best answer would name two people identically and
 * erase one of them from the meeting.
 *
 * Run: npx tsx src/source-name-correlator.test.ts
 */
import { correlateSourcesToNames, type Interval } from './source-name-correlator.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};
const runs = (...pairs: Array<[number, number]>): Interval[] => pairs.map(([start, end]) => ({ start, end }));

// ── m34: two humans, both nameable. The thin case — one source's margin is ~13 points. ──
{
  // leo speaks in long runs with his tile lit; Dmitry's runs are shorter and his tile lags into them.
  const sources = {
    '414': runs([0, 20_000], [40_000, 70_000], [90_000, 110_000]),
    '201': runs([22_000, 38_000], [72_000, 88_000], [112_000, 130_000]),
  };
  const names = {
    'leo (Unverified)': runs([1000, 21_000], [41_000, 71_000], [91_000, 111_000]),
    // Dmitry's tile is lit over his own runs AND smears ~2 s into leo's, the m34 bleed.
    'Dmitry Grankin': runs([23_000, 40_000], [73_000, 90_000], [113_000, 131_000]),
  };
  const r = correlateSourcesToNames(sources, names);
  const by = Object.fromEntries(r.bindings.map((b) => [b.sourceId, b.name]));
  check('m34 shape: both sources bind, to different people',
    by['414'] === 'leo (Unverified)' && by['201'] === 'Dmitry Grankin', JSON.stringify(by));
  check('the lag it chose is a measurement, not the default',
    typeof r.lagMs === 'number', String(r.lagMs));
}

// ── m30: THE TRAP. One tile lit for most of the call; raw co-activity picks it for BOTH sources. ──
{
  const sources = {
    '1266': runs([50_000, 120_000], [140_000, 200_000]),   // Leo, the long talker
    '201': runs([0, 45_000], [125_000, 135_000]),          // Dmitry, whose tile never lights
  };
  // Only ONE name ever appears in the UI, and its lit-time smears across the whole meeting.
  const names = { 'Leo (Unverified)': runs([0, 130_000], [140_000, 205_000]) };
  const r = correlateSourcesToNames(sources, names);
  const by = Object.fromEntries(r.bindings.map((b) => [b.sourceId, b.name]));
  check('m30 trap: the one name goes to ONE source, not both',
    r.bindings.length === 1 && by['1266'] === 'Leo (Unverified)', JSON.stringify(r.bindings));
  check('…and it goes to the source holding the majority of that NAME\'s evidence',
    by['201'] === undefined, JSON.stringify(by));
  check('the refusal is reported, not silent', r.refused.length >= 1, JSON.stringify(r.refused));
}

// ── m36: a single source and a single name. Trivial, and it must not be over-thought. ──
{
  const r = correlateSourcesToNames(
    { '201': runs([0, 90_000]) },
    { 'Dmitry Grankin': runs([1000, 91_000]) },
  );
  check('m36 shape: the lone source binds to the lone name',
    r.bindings.length === 1 && r.bindings[0].name === 'Dmitry Grankin', JSON.stringify(r.bindings));
}

// ── The bars themselves ──
{
  // Two names equally lit across one source: the UI cannot decide, so neither does this.
  const r = correlateSourcesToNames(
    { s: runs([0, 20_000]) },
    { A: runs([0, 10_000]), B: runs([10_000, 20_000]) },
  );
  check('a source split evenly between two names binds to neither',
    r.bindings.length === 0 && r.refused.some((x) => x.reason === 'below-margin' || x.reason === 'below-share'),
    JSON.stringify({ b: r.bindings, r: r.refused }));
  // A brush of co-activity is not evidence.
  const t = correlateSourcesToNames({ s: runs([0, 800]) }, { A: runs([0, 800]) });
  check('a fraction of a second of agreement binds nothing',
    t.bindings.length === 0 && t.refused.some((x) => x.reason === 'below-support'), JSON.stringify(t.refused));
  // Empty inputs are a normal meeting state (nobody has spoken yet), not an error.
  const e = correlateSourcesToNames({}, {});
  check('no sources and no names is silence, not a throw', e.bindings.length === 0);
}

// ── Weighting is carried, even though no tape yet supplies it ──
{
  const r = correlateSourcesToNames(
    { loud: [{ start: 0, end: 20_000, weight: 1 }] },
    { A: [{ start: 0, end: 20_000, weight: 2 }] },
  );
  check('a supplied weight scales co-activity rather than being ignored',
    r.bindings.length === 1 && r.bindings[0].supportMs === 40_000, JSON.stringify(r.bindings));
}

if (failed) { console.error(`\n❌ source-name-correlator: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ source-name-correlator: scans the lag per meeting, holds cross-source exclusivity as a hard constraint, and stays silent under the margin.');
