/**
 * Namer-only corpus replay — drives ONE `TrackNamer` over ONE tape's namer inputs.
 *
 * The namer consumes csrc edges, DOM hints, roster sightings and audio ticks. It consumes no
 * audio and no transcript, so a tape replays as pure metadata: `normalize.py` strips the PCM
 * before anything reaches this file. That is what makes a corpus-wide A/B affordable — 25 tapes
 * replay in seconds, with no STT and no meeting content on disk.
 *
 * The namer implementation under test is injected by path so the SAME driver replays the
 * pre-fix and post-fix namer. Anything else (a second checkout, a second harness) would let the
 * two arms diverge for reasons unrelated to the change.
 *
 *   NAMER=/abs/path/to/track-namer.ts TAPE=/abs/path/to/norm/<id> npx tsx replay.ts
 *
 * Output (stdout, JSON): per-track final name + label, time-to-first-name, time-to-final-name,
 * and the full label event stream — which is where a provisional name that was later withdrawn
 * shows up as a retraction.
 */
import { readFileSync } from 'node:fs';

const TAPE = process.env.TAPE;
const NAMER = process.env.NAMER;
if (!TAPE || !NAMER) throw new Error('TAPE and NAMER env vars are required');

const { TrackNamer } = (await import(NAMER)) as any;

const jl = (f: string): any[] => {
  try {
    return readFileSync(`${TAPE}/${f}`, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  } catch {
    return [];
  }
};

const meta = JSON.parse(readFileSync(`${TAPE}/meta.json`, 'utf8'));
const csrc = jl('csrc.jsonl');
const hints = jl('hints.jsonl');
const roster = jl('roster.jsonl');
const ticks = jl('ticks.jsonl');

type Ev = { t: number; ord: number; fn: () => void };
const evs: Ev[] = [];
const events: Array<{ track: string; name: string; atMs: number }> = [];
let cur = 0;

// Constructed exactly as `TeamsCsrcGmeetPipeline` constructs it (mixed-pipeline/src/
// teams-csrc-gmeet-pipeline.ts). A replay that configures the namer differently from production
// measures a namer production never runs.
const namer = new TrackNamer({
  selfName: 'Vexa',
  requireCanonicalDisplayName: true,
  onNamed: (trackId: string, name: string) => events.push({ track: trackId, name, atMs: cur }),
});

// ord keeps same-millisecond events in the pipeline's own order: transport edge, then the DOM
// signals that describe it, then the clock. Sorting on `t` alone would make the replay depend on
// file order rather than on the tape.
for (const r of csrc) evs.push({ t: r.t, ord: 0, fn: () => namer.setTrackActive(String(r.csrc), r.active, r.t) });
for (const h of hints) evs.push({ t: h.t, ord: 1, fn: () => namer.recordHint(h.name, h.t, h.isEnd === true) });
for (const r of roster) {
  evs.push({
    t: r.t,
    ord: 1,
    fn: () => {
      if (r.k === 'roster-name') namer.recordRosterName(r.name, r.t);
      else namer.recordRosterCoverage(r.named, r.participants, r.t);
    },
  });
}
for (const a of ticks) evs.push({ t: a.ts, ord: 2, fn: () => namer.tick(a.ts + (a.samples / 16000) * 1000) });

evs.sort((x, y) => x.t - y.t || x.ord - y.ord);

// CUT_MS drops every event before an absolute timestamp. That is the settled-window CONTROL:
// replay with the roster's discovery window excluded and the premature-acceptance path cannot
// fire, so whatever names come out are what the evidence says without the timing hazard. It is
// one of the three independent lines the corpus uses to establish per-track truth.
const CUT = Number(process.env.CUT_MS ?? 0);
const kept = evs.filter((e) => e.t >= CUT);
if (kept.length === 0) throw new Error('no events after CUT_MS');
const t0 = kept[0].t;
for (const e of kept) {
  cur = e.t;
  e.fn();
}
namer.finish();

const rel = (ms: number) => Math.round((ms - t0) / 100) / 10;
const tracks: Record<string, any> = {};
for (const id of meta.tracks as string[]) {
  const mine = events.filter((e) => e.track === id);
  const named = mine.filter((e) => !/^Speaker [A-Z]$/.test(e.name));
  // A retraction is a published HUMAN name that a later event replaced — by another human name or
  // by a fallback Speaker-N. Under the pre-fix namer an evidence name was permanent, so this list
  // is empty by construction; under the fix it is the visible cost of publishing a provisional
  // name, and the scorecard prices it rather than assuming it is free.
  const retractions = mine
    .map((e, i) => ({ e, next: mine[i + 1] }))
    .filter(({ e, next }) => !/^Speaker [A-Z]$/.test(e.name) && next && next.name !== e.name)
    .map(({ e, next }) => ({ from: e.name, to: next.name, atS: rel(next.atMs) }));
  tracks[id] = {
    finalName: namer.nameFor(id),
    label: namer.labelFor(id),
    naming: namer.naming(id),
    firstNameAtS: named.length ? rel(named[0].atMs) : null,
    firstNameWas: named.length ? named[0].name : null,
    finalNameAtS: named.length ? rel(named[named.length - 1].atMs) : null,
    retractions,
    events: mine.map((e) => ({ name: e.name, atS: rel(e.atMs) })),
  };
}

process.stdout.write(
  JSON.stringify({ tape: meta.id, namer: NAMER, cutMs: CUT || null, tracks, stats: namer.stats() }, null, 1),
);
