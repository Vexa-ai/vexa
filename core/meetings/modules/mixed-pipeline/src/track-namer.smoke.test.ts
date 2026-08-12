/**
 * track-namer — a transport track earns a name, or it keeps a letter.
 *
 * The last check is the one worth reading. It replays the SHAPE of the m30 fixture: two sources in
 * the mix, and a DOM that named exactly ONE of them all meeting. Because the tiles lag the audio by
 * about a second, the other source accrues real-looking evidence for that same name (7.5s against
 * 61s on the actual fixture) — so a namer that simply took each track's best candidate would
 * confidently label BOTH tracks "leo", producing two wrong names out of correct data and erasing a
 * participant. Exclusivity is what stops that, and the check exists so it cannot be tuned away.
 *
 * Run: npx tsx src/track-namer.smoke.test.ts
 */
import { TrackNamer, speakerLabel } from './track-namer.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

/** A namer with the lag switched OFF, so a test can state times in audio terms. */
const namer = (over: Partial<NonNullable<ConstructorParameters<typeof TrackNamer>[0]>> = {}) =>
  new TrackNamer({ settleMs: 0, minEpisodeMs: 600, corroborations: 2, rosterSettleMs: 5000, ...over });

// ── 1) Stable letters when nothing names anybody ────────────────────────────────────────────────
{
  check('the letters run A, B, … Z, AA', speakerLabel(0) === 'Speaker A' && speakerLabel(1) === 'Speaker B'
    && speakerLabel(25) === 'Speaker Z' && speakerLabel(26) === 'Speaker AA', speakerLabel(26));
  const n = namer();
  n.setTrackActive('55', true, 0);
  n.setTrackActive('11', true, 5000);
  check('letters follow FIRST-HEARD order, not the transport ids',
    n.labelFor('55') === 'Speaker A' && n.labelFor('11') === 'Speaker B', `${n.labelFor('55')} / ${n.labelFor('11')}`);
  check('a track with no evidence is never named', n.nameFor('55') === null);
}

// ── 2) A name is earned from exclusive coincidence, and only after corroboration ─────────────────
{
  const named: Array<[string, string]> = [];
  const n = namer({ onNamed: (t, nm) => named.push([t, nm]) });
  // Episode 1: track 1 alone, "Ana" alone.
  n.setTrackActive('1', true, 0);
  n.recordHint('Ana', 1000);            // lag-corrected to 0; grace 2500
  n.setTrackActive('1', false, 2000);
  n.tick(3000);
  check('ONE coincidence is a coincidence, not a name', n.nameFor('1') === null, JSON.stringify(n.stats()));
  // Episode 2.
  n.setTrackActive('1', true, 10_000);
  n.recordHint('Ana', 11_000);
  n.setTrackActive('1', false, 12_000);
  n.tick(20_000);
  check('the second episode earns it', n.nameFor('1') === 'Ana', JSON.stringify(n.stats()));
  check('the name is announced once, for the retroactive repaint',
    named.length === 1 && named[0][0] === '1' && named[0][1] === 'Ana', JSON.stringify(named));
  check('a named track reports its name, not its letter', n.labelFor('1') === 'Ana');
}

// ── 3) Ambiguity contributes NOTHING — not a weaker vote, nothing ────────────────────────────────
{
  const n = namer();
  // Two tiles lit at once for the whole span: Teams' known weakness, and unresolvable from the UI.
  n.setTrackActive('1', true, 0);
  for (const t of [1000, 3000, 5000, 7000, 9000]) { n.recordHint('Ana', t); n.recordHint('Bo', t); }
  n.setTrackActive('1', false, 12_000);
  n.tick(20_000);
  check('two tiles lit at once name nobody', n.nameFor('1') === null, JSON.stringify(n.stats()));

  const m = namer();
  // Two sources audible at once: the mix cannot say which of them the lit tile belongs to.
  m.setTrackActive('1', true, 0);
  m.setTrackActive('2', true, 0);
  for (const t of [1000, 3000, 5000, 7000, 9000]) m.recordHint('Ana', t);
  m.setTrackActive('1', false, 12_000);
  m.setTrackActive('2', false, 12_000);
  m.tick(20_000);
  check('two sources audible at once name nobody either',
    m.nameFor('1') === null && m.nameFor('2') === null, JSON.stringify(m.stats()));
}

// ── 4) THE m30 SHAPE: one name in the DOM, two sources in the mix ────────────────────────────────
{
  const n = namer();
  // Leo speaks in long solo runs while his tile is lit. Dmitry speaks too, but his tile NEVER
  // lights (m30: `outline-missing` for the first 44s, and his name is absent from the tape
  // entirely). The DOM's 1s lag means Leo's tile is still lit as Dmitry starts, which is where the
  // false evidence for Dmitry's track comes from.
  const leoRun = (t0: number, durMs: number): void => {
    n.setTrackActive('1266', true, t0);
    for (let t = t0; t < t0 + durMs; t += 2000) n.recordHint('leo (Unverified)', t + 1000);   // +1s lag
    n.recordHint('leo (Unverified)', t0 + durMs);   // the last tile refresh lands AFTER he stops
    n.setTrackActive('1266', false, t0 + durMs);
  };
  const dmitryRun = (t0: number, durMs: number): void => {
    n.setTrackActive('201', true, t0);
    n.setTrackActive('201', false, t0 + durMs);
  };
  // Leo, then Dmitry immediately after — so Leo's lag-shifted tile bleeds into Dmitry's run.
  for (let i = 0; i < 6; i++) { leoRun(i * 20_000, 8000); dmitryRun(i * 20_000 + 8200, 6000); }
  n.tick(200_000);
  const ev = n.stats().evidence;
  check('the fixture shape DID leak evidence for the unnamed track (the trap is real)',
    (ev['201']?.['leo (Unverified)'] ?? 0) > 0, JSON.stringify(ev));
  check('1266 is leo', n.nameFor('1266') === 'leo (Unverified)', JSON.stringify(ev));
  check('201 is NOT leo — the name belongs to the track holding the clear majority of its evidence',
    n.nameFor('201') === null, `${n.nameFor('201')} · ${JSON.stringify(ev)}`);
  check('201 publishes as a distinct person under a letter, never erased and never guessed',
    /^Speaker [A-Z]+$/.test(n.labelFor('201')) && n.labelFor('201') !== n.labelFor('1266'),
    `${n.labelFor('201')} vs ${n.labelFor('1266')}`);
}

// ── 5) THE ELIMINATION RULE — and everything it must refuse ─────────────────────────────────────
{
  // The m30 story exactly: two tracks, and a roster that knows both people while the tiles only
  // ever light for one of them. Leo is named from evidence; Dmitry can only be reached by
  // elimination, because nothing in the meeting ever says his name AND a time together.
  const named: Array<[string, string]> = [];
  const n = namer({ onNamed: (t, nm) => named.push([t, nm]), rosterSightings: 2 });
  n.noteHeard('201');
  n.noteHeard('1266');
  for (const nm of ['leo (Unverified)', 'Dmitry Grankin']) { n.recordRosterName(nm, 0); n.recordRosterName(nm, 100); }
  n.tick(6000);   // past the roster settle, before any speaking evidence exists
  check('two unnamed tracks and two unclaimed names ⇒ elimination REFUSES',
    n.nameFor('201') === null && n.nameFor('1266') === null, JSON.stringify(n.stats().how));
  // Now Leo earns 1266 from real evidence.
  for (const t0 of [10_000, 30_000]) {
    n.setTrackActive('1266', true, t0);
    for (let t = t0; t < t0 + 4000; t += 1000) n.recordHint('leo (Unverified)', t + 1000);
    n.setTrackActive('1266', false, t0 + 4000);
  }
  n.tick(60_000);
  check('1266 is leo, from evidence', n.naming('1266')?.source === 'evidence', JSON.stringify(n.stats().how));
  check('…and 201 is now the ONLY unnamed track against the ONLY unclaimed name ⇒ it fires',
    n.nameFor('201') === 'Dmitry Grankin' && n.naming('201')?.source === 'elimination',
    JSON.stringify(n.stats().how));
  check('the elimination is announced, so what it named gets repainted like any other name',
    named.some(([t, nm]) => t === '201' && nm === 'Dmitry Grankin'), JSON.stringify(named));
}
{
  // THE TRAP. Three people, three names, nobody nameable from evidence. A rule that paired anything
  // here would be printing a human's name off a coin toss.
  const n = namer({ rosterSightings: 2 });
  for (const t of ['a', 'b', 'c']) n.noteHeard(t);
  for (const nm of ['Ana', 'Bo', 'Cy']) { n.recordRosterName(nm, 0); n.recordRosterName(nm, 100); }
  n.tick(30_000);
  check('3 unnamed tracks + 3 unclaimed names ⇒ NOTHING fires',
    ['a', 'b', 'c'].every((t) => n.nameFor(t) === null), JSON.stringify(n.stats().how));
  // Two named leaves one track and one name — that IS decidable.
  n.recordRosterName('Ana', 200);
  const m = namer({ rosterSightings: 2 });
  m.noteHeard('a'); m.noteHeard('b');
  for (const nm of ['Ana', 'Bo', 'Cy']) { m.recordRosterName(nm, 0); m.recordRosterName(nm, 100); }
  m.tick(30_000);
  check('one unnamed track but TWO unclaimed names ⇒ still refuses (the other direction)',
    m.nameFor('a') === null && m.nameFor('b') === null, JSON.stringify(m.stats().how));
}
{
  // A roster name sighted once is not a participant — a rotting selector can produce one.
  const n = namer({ rosterSightings: 2 });
  n.noteHeard('solo');
  n.recordRosterName('Flicker', 0);
  check('an uncorroborated roster name cannot pair with anything',
    n.nameFor('solo') === null, JSON.stringify(n.stats().roster));
  n.recordRosterName('Flicker', 100);
  n.tick(60_000);   // the room settled and nobody new appeared
  check('a second sighting plus a settled roster makes it usable',
    n.nameFor('solo') === 'Flicker', JSON.stringify(n.stats().how));
  // THE RACE. Sightings arrive one name at a time, so a roster mid-fill briefly looks like a
  // decidable 1-and-1. Eliminating there is a coin toss wearing an argument's clothes — and on the
  // real m30 tape it produced the RIGHT answer for the WRONG reason, which is how it hid.
  const r = namer({ rosterSightings: 2 });
  r.noteHeard('only');
  r.recordRosterName('First Name', 0);
  r.recordRosterName('First Name', 10);      // corroborated…
  check('a half-filled roster cannot decide anything, even at 1 track and 1 qualified name',
    r.nameFor('only') === null, JSON.stringify(r.stats()));
  r.recordRosterName('Second Name', 20);     // …and now a second person appears
  r.recordRosterName('Second Name', 30);
  r.tick(60_000);
  check('the second name lands and the pairing is correctly ambiguous',
    r.nameFor('only') === null, JSON.stringify(r.stats().how));
}
{
  // Elimination never overrides evidence: a named track is never revisited.
  const n = namer({ rosterSightings: 2 });
  n.setTrackActive('1', true, 0);
  n.recordHint('Ana', 1000);
  n.setTrackActive('1', false, 2000);
  n.setTrackActive('1', true, 10_000);
  n.recordHint('Ana', 11_000);
  n.setTrackActive('1', false, 12_000);
  n.tick(20_000);
  for (const nm of ['Ana', 'Someone Else']) { n.recordRosterName(nm, 0); n.recordRosterName(nm, 100); }
  n.tick(60_000);
  check('a track named from evidence is never re-let by elimination',
    n.nameFor('1') === 'Ana' && n.naming('1')?.source === 'evidence', JSON.stringify(n.stats().how));
}
{
  // Casing comes from the roster, and ONLY from the roster. The " (Unverified)" suffix is Teams'
  // own statement about the participant and is never tidied away.
  const n = namer({ rosterSightings: 2 });
  n.setTrackActive('9', true, 0);
  n.recordHint('leo (Unverified)', 1000);
  n.setTrackActive('9', false, 2000);
  n.setTrackActive('9', true, 10_000);
  n.recordHint('leo (Unverified)', 11_000);
  n.setTrackActive('9', false, 12_000);
  n.tick(20_000);
  check('before the roster speaks, the tile\'s own casing stands', n.nameFor('9') === 'leo (Unverified)');
  n.recordRosterName('Leo (Unverified)', 100);
  check('the roster\'s canonical casing is adopted, suffix intact',
    n.nameFor('9') === 'Leo (Unverified)', String(n.nameFor('9')));
  const m = namer({ rosterSightings: 2 });
  m.setTrackActive('9', true, 0);
  m.recordHint('bob smith', 1000);
  m.setTrackActive('9', false, 2000);
  m.setTrackActive('9', true, 10_000);
  m.recordHint('bob smith', 11_000);
  m.setTrackActive('9', false, 12_000);
  m.tick(20_000);
  check('with no roster sighting, no capitalisation is invented', m.nameFor('9') === 'bob smith', String(m.nameFor('9')));
}

// ── 6) A POLLUTED OR PARTIAL ROSTER DISABLES ELIMINATION (the m34 failure) ──────────────────────
{
  // What the live m34 meeting actually contained: two humans, an outline that named one of them,
  // and a roster holding OUR OWN BOT. Elimination had exactly one track and one name left, and put
  // the bot's name on the other human's speech. One unusable entry now disables the rule outright —
  // "the only name left" is an argument about people, and this set is not one.
  const n = namer({ rosterSightings: 2, selfName: 'Vexa' });
  n.noteHeard('414'); n.noteHeard('201');
  for (const nm of ['leo (Unverified)', 'Vexa (Unverified)']) { n.recordRosterName(nm, 0); n.recordRosterName(nm, 10); }
  for (const t0 of [10_000, 30_000]) {
    n.setTrackActive('414', true, t0);
    for (let t = t0; t < t0 + 4000; t += 1000) n.recordHint('leo (Unverified)', t + 1000);
    n.setTrackActive('414', false, t0 + 4000);
  }
  n.tick(60_000);
  check('the bot is refused entry to the roster at all',
    !Object.keys(n.stats().roster).some((x) => /vexa/i.test(x)), JSON.stringify(n.stats().roster));
  check('and its presence is RECORDED, not silently dropped',
    n.stats().rosterPolluted.length === 1, JSON.stringify(n.stats().rosterPolluted));
  check('leo is still named from real evidence', n.naming('414')?.source === 'evidence', JSON.stringify(n.stats().how));
  check('THE m34 FAILURE: the other human is NOT given the bot\'s name',
    n.nameFor('201') === null && /^Speaker [A-Z]+$/.test(n.labelFor('201')), String(n.nameFor('201')));
}
{
  // The completeness premise, on its own — a CLEAN roster, one unnamed track, one unclaimed name,
  // and the producer reporting that it could not name everyone it could see. m34's scans saw four
  // tiles and resolved two, and the people it could not name included the human the rule then
  // mislabelled. A roster that is missing people cannot support "the only one left".
  const n = namer({ rosterSightings: 2 });
  n.noteHeard('a'); n.noteHeard('b');
  for (const nm of ['Ana', 'Bo']) { n.recordRosterName(nm, 0); n.recordRosterName(nm, 10); }
  for (const t0 of [10_000, 30_000]) {
    n.setTrackActive('a', true, t0);
    for (let t = t0; t < t0 + 4000; t += 1000) n.recordHint('Ana', t + 1000);
    n.setTrackActive('a', false, t0 + 4000);
  }
  n.recordRosterCoverage(2, 4, 40_000);   // saw four participants, could name two
  n.tick(60_000);
  check('a PARTIAL roster refuses elimination even when it looks decidable',
    n.naming('a')?.source === 'evidence' && n.nameFor('b') === null, JSON.stringify(n.stats().how));
  // …and the same set, once the producer says it read everyone, does decide.
  const m = namer({ rosterSightings: 2 });
  m.noteHeard('a'); m.noteHeard('b');
  for (const nm of ['Ana', 'Bo']) { m.recordRosterName(nm, 0); m.recordRosterName(nm, 10); }
  for (const t0 of [10_000, 30_000]) {
    m.setTrackActive('a', true, t0);
    for (let t = t0; t < t0 + 4000; t += 1000) m.recordHint('Ana', t + 1000);
    m.setTrackActive('a', false, t0 + 4000);
  }
  m.recordRosterCoverage(2, 2, 40_000);
  m.tick(60_000);
  check('a COMPLETE roster still decides — the gate is completeness, not timidity',
    m.nameFor('b') === 'Bo' && m.naming('b')?.source === 'elimination', JSON.stringify(m.stats().how));
}
{
  // The placeholder never becomes a name on ANY path — it arrives shaped exactly like one.
  const n = namer({ rosterSightings: 2, selfName: 'Vexa' });
  n.setTrackActive('1', true, 0);
  for (let t = 0; t < 4000; t += 1000) { n.recordHint('Unknown user', t + 1000); n.recordCaption('Unknown user', t + 1000); }
  n.setTrackActive('1', false, 4000);
  n.setTrackActive('1', true, 10_000);
  for (let t = 10_000; t < 14_000; t += 1000) n.recordCaption('Unknown user', t + 1000);
  n.setTrackActive('1', false, 14_000);
  n.tick(30_000);
  check('"Unknown user" contributes no evidence and can never be a speaker',
    n.nameFor('1') === null && Object.keys(n.stats().evidence).length === 0, JSON.stringify(n.stats().evidence));
  const b = namer({ rosterSightings: 2, selfName: 'Vexa' });
  b.setTrackActive('1', true, 0);
  for (let t = 0; t < 4000; t += 1000) b.recordHint('Vexa (Unverified)', t + 1000);
  b.setTrackActive('1', false, 4000);
  b.tick(30_000);
  check('our own bot, however Teams qualifies its name, is never a speaker either',
    b.nameFor('1') === null, String(b.nameFor('1')));
}

// ── 7) Captions are a second, independent naming source ─────────────────────────────────────────
{
  const n = namer();
  n.setTrackActive('4', true, 0);
  n.recordCaption('Priya Nair', 2000);      // paints [0, 1500] after the 1s lag
  n.setTrackActive('4', false, 4000);
  n.setTrackActive('4', true, 10_000);
  n.recordCaption('Priya Nair', 12_000);
  n.setTrackActive('4', false, 14_000);
  n.tick(20_000);
  check('a track can be named by the platform captions alone (no DOM tile at all)',
    n.nameFor('4') === 'Priya Nair', JSON.stringify(n.stats()));
}

// ── 8) A SECOND BOT IS NOT A PERSON (meeting 25930) ─────────────────────────────────────────────
// Two of our bots in one room. The DOM yielded nothing, so the roster was the only name source —
// and it held exactly one name: the OTHER bot. One track, one name, corroborated and settled: every
// gate elimination has, passed. It bound a human's speech to "Vexa (Unverified)". The self-name
// check could not catch it because it asks "is this name MINE" (`vexa test`), not "is it MY KIND".
{
  const seen: string[] = [];
  const n = namer({ selfName: 'Vexa test', onObservation: (o) => seen.push(`${o.type}:${o.name}`) });
  n.setTrackActive('201', true, 0);
  for (const t of [0, 100, 200]) n.recordRosterName('Vexa (Unverified)', t);
  n.recordRosterCoverage(1, 1, 200);           // the producer counts the bot as a named participant
  n.setTrackActive('201', false, 8000);
  n.tick(60_000);                              // well past the roster settle window
  check('a second bot in the roster never wins by elimination',
    n.nameFor('201') !== 'Vexa (Unverified)', `got ${n.nameFor('201')}`);
  const held = n.nameFor('201');
  check('the track holds no name at all, so the publish path gives it a letter',
    !held || /^Speaker /.test(held), `${held}`);
  check('the refusal is emitted as a typed observation, so a tape shows it',
    seen.includes('bot-family-in-roster:Vexa (Unverified)'), JSON.stringify(seen));
}

// ── 9) …and a roster of ONLY bots is INCOMPLETE, not complete ───────────────────────────────────
// named=1/participants=1 is the most complete a roster can look, and it was the state elimination
// trusted most. Discounting the bot-family entries tells the truth: nobody has been named yet.
{
  const n = namer({ selfName: 'Vexa test' });
  for (const t of [0, 100, 200]) n.recordRosterName('Vexa (Unverified)', t);
  n.recordRosterCoverage(1, 1, 200);
  const cov = n.stats().rosterCoverage;
  check('a roster holding only bot-family entries reads as coverage-incomplete',
    !cov || cov.named === 0, JSON.stringify(cov));
}

// ── 10) SCOPE: the exclusion gates elimination, never direct evidence ────────────────────────────
// The misfire cost has to stay "fewer eliminations in a weird room", never "a human who cannot be
// named". Someone actually called Vexa still earns their name from exclusive coincidence.
{
  const n = namer({ selfName: 'Vexa test' });
  n.setTrackActive('77', true, 0);
  for (let t = 0; t < 8000; t += 1000) n.recordHint('Vexa Petrova', t + 1000);
  n.setTrackActive('77', false, 9000);
  n.tick(20_000);
  check('a real human whose name shares the bot stem is still named by direct evidence',
    n.nameFor('77') === 'Vexa Petrova', `got ${n.nameFor('77')}`);
}

if (failed) { console.error(`\n❌ track-namer: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ track-namer: a track is named only from unambiguous, corroborated, exclusively-held evidence — and otherwise keeps a stable letter.');
