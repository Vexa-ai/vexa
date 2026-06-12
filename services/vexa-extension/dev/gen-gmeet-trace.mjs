/**
 * Deterministic multi-speaker trace generator — attribution testing with NO
 * multi-speaker call (and no call at all).
 *
 * Synthesizes a GmeetTrace from a scenario spec, using REAL Google Meet tile
 * class strings captured live (2026-06-10, bot SpeakerDebug): a speaking tile
 * carried [IisKdb GF8M7d Oaajhc YFyDbd iPFm3e VeFZv]; 'Oaajhc' is the speaking
 * indicator, 'gjg47c' the silence class. So the replayed DOM exercises the
 * exact selector matching production sees.
 *
 * Usage:
 *   node dev/gen-gmeet-trace.mjs > /tmp/gen.trace.json
 *   node dev/test-gmeet-replay.mjs /tmp/gen.trace.json 5
 *
 * Or import { generateTrace, SCENARIOS } from another script.
 */

const BASE_CLASSES = 'IisKdb GF8M7d YFyDbd iPFm3e VeFZv';
const SPEAKING = 'Oaajhc';
const SILENT = 'gjg47c';

/**
 * spec: {
 *   selfName, speakers: [names...],   // speakers[i] uses track index i
 *   turns: [{ speaker: idx | idx[], start: ms, end: ms }],  // who audibly speaks when
 *   joins?: [{ speaker: idx, at: ms }],   // tile appears later (default: all at 0)
 *   durationMs
 * }
 * Ground truth: every track index that got >= 2 clean solo turns must lock to its name.
 */
export function generateTrace(spec) {
  const events = [];
  const present = new Set(spec.speakers.map((_, i) => i));
  const joinAt = new Map();
  for (const j of spec.joins || []) { present.delete(j.speaker); joinAt.set(j.speaker, j.at); }

  const speakingAt = (t) => {
    const active = new Set();
    for (const turn of spec.turns) {
      if (t >= turn.start && t < turn.end) {
        for (const s of Array.isArray(turn.speaker) ? turn.speaker : [turn.speaker]) active.add(s);
      }
    }
    return active;
  };

  const tilesAt = (t) => {
    const tiles = [{
      id: 'self-tile', name: spec.selfName, self: true,
      rootClasses: `${BASE_CLASSES} ${SILENT}`, indicatorClasses: [],
    }];
    const active = speakingAt(t);
    spec.speakers.forEach((name, i) => {
      const joined = !joinAt.has(i) || t >= joinAt.get(i);
      if (!joined) return;
      const speaking = active.has(i);
      tiles.push({
        id: `tile-${i}`, name, self: false,
        rootClasses: `${BASE_CLASSES} ${speaking ? SPEAKING : SILENT}`,
        indicatorClasses: speaking ? [SPEAKING] : [],
      });
    });
    return tiles;
  };

  // Tile snapshots: emit at every state-change boundary (turn edges, joins)
  const edges = new Set([0]);
  for (const turn of spec.turns) { edges.add(turn.start); edges.add(turn.end); }
  for (const j of spec.joins || []) edges.add(j.at);
  for (const t of [...edges].sort((a, b) => a - b)) {
    events.push({ t, kind: 'tiles', tiles: tilesAt(t) });
  }

  // Audio events: one per 100ms per audible track during its turns
  for (const turn of spec.turns) {
    for (const s of Array.isArray(turn.speaker) ? turn.speaker : [turn.speaker]) {
      for (let t = turn.start; t < turn.end; t += 100) {
        events.push({ t: t + 30, kind: 'audio', track: s });
      }
    }
  }
  events.sort((a, b) => a.t - b.t);

  // Ground truth: tracks with >=2 solo turns lock to their names
  const soloTurns = new Map();
  for (const turn of spec.turns) {
    const ss = Array.isArray(turn.speaker) ? turn.speaker : [turn.speaker];
    if (ss.length === 1) soloTurns.set(ss[0], (soloTurns.get(ss[0]) || 0) + 1);
  }
  const finalLocks = {};
  for (const [s, n] of soloTurns) if (n >= 2) finalLocks[s] = spec.speakers[s];

  return {
    version: 1,
    selfName: spec.selfName,
    durationMs: spec.durationMs,
    params: { pollMs: 500, audioWindowMs: 700, lockThreshold: 2, lockRatio: 0.7 },
    events,
    finalLocks,
  };
}

export const SCENARIOS = {
  // 5 external speakers + self: round-robin, 3s turns, two rounds
  six_party_round_robin: () => {
    const speakers = ['Alice Chen', 'Bob Kumar', 'Carol Diaz', 'Dave Okafor', 'Eve Lindqvist'];
    const turns = [];
    let t = 1000;
    for (let round = 0; round < 2; round++) {
      for (let s = 0; s < speakers.length; s++) {
        turns.push({ speaker: s, start: t, end: t + 3000 });
        t += 3500;
      }
    }
    return generateTrace({ selfName: 'Me Myself', speakers, turns, durationMs: t + 1000 });
  },

  // Overlapping pairs: A+B talk together, then solo turns resolve them
  overlap_then_resolve: () => {
    const speakers = ['Alice Chen', 'Bob Kumar'];
    const turns = [
      { speaker: [0, 1], start: 1000, end: 4000 },   // overlap: half votes only
      { speaker: 0, start: 5000, end: 8000 },
      { speaker: 1, start: 9000, end: 12000 },
      { speaker: 0, start: 13000, end: 16000 },
      { speaker: 1, start: 17000, end: 20000 },
    ];
    return generateTrace({ selfName: 'Me Myself', speakers, turns, durationMs: 21000 });
  },

  // Late joiner mid-meeting must still lock; early locks must survive the join
  late_joiner: () => {
    const speakers = ['Alice Chen', 'Bob Kumar', 'Carol Diaz'];
    const turns = [
      { speaker: 0, start: 1000, end: 4000 },
      { speaker: 0, start: 5000, end: 8000 },
      { speaker: 1, start: 9000, end: 12000 },
      { speaker: 1, start: 13000, end: 16000 },
      { speaker: 2, start: 19000, end: 22000 },      // joins at 17s, then speaks
      { speaker: 2, start: 23000, end: 26000 },
    ];
    return generateTrace({
      selfName: 'Me Myself', speakers, turns,
      joins: [{ speaker: 2, at: 17000 }], durationMs: 27000,
    });
  },
};

// CLI: node gen-gmeet-trace.mjs [scenario] → JSON to stdout
const name = process.argv[2] || 'six_party_round_robin';
if (import.meta.url === `file://${process.argv[1]}`) {
  const gen = SCENARIOS[name];
  if (!gen) { console.error(`unknown scenario "${name}". Available: ${Object.keys(SCENARIOS).join(', ')}`); process.exit(1); }
  process.stdout.write(JSON.stringify(gen()));
}
