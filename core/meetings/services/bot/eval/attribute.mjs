#!/usr/bin/env node
/**
 * attribute.mjs — when a run goes RED, name the upstream @vexa/* brick that owns the symptom and
 * print the offline-reproduction command. The bot is a COMPOSITION of bricks; a failing metric is a
 * symptom in exactly one of them. This separates concerns so the right module gets debugged — not
 * the bot, and not a flaky live Meet.
 *
 * Input (one of):
 *   • a flagged-issue.v1 array  (analyze.mjs --flag-out)  → maps issue_type → brick.
 *   • the empty case            (--empty, or an empty array) → the bot produced 0 segments; the fault
 *                                 is UPSTREAM of attribution. Disambiguate with the lifecycle:
 *                                 --status <last lifecycle.v1 status> (joining|awaiting_admission|active|failed).
 *
 * Usage:
 *   node attribute.mjs <flags.json>
 *   node attribute.mjs --empty --status active     # active but no transcript → capture or STT
 *
 * O-TEL hook: a 'mis-attribution'/'oversegment' issue carrying a captured-signal.v1 `signal` link
 * reproduces OFFLINE + deterministically through the REAL gmeet pipeline — the gate:replay path
 * (`pnpm --filter @vexa/bot run replay`, services/bot/src/replay.test.ts). No live meeting, no STT
 * model, no server — the exact module is debugged in isolation.
 */
import { readFileSync } from 'node:fs';

const ARGV = process.argv.slice(2);
const EMPTY = ARGV.includes('--empty');
const statusIx = ARGV.indexOf('--status');
const STATUS = statusIx >= 0 ? ARGV[statusIx + 1] : null;
const file = ARGV.find((a) => !a.startsWith('--') && a !== STATUS);

// issue_type → the brick that owns it + how to confirm it.
const BRICK = {
  'mis-attribution': {
    brick: '@vexa/gmeet-pipeline (gmeet-channel-binder — speaker↔channel attribution)',
    why: 'content self-IDs speaker X but the segment is labelled Y — a channel/glow mis-bind (cf. BASELINE.md host-name glow-leak).',
  },
  oversegment: {
    brick: '@vexa/gmeet-pipeline (segmentation — LocalAgreement / pyannote despeckle)',
    why: 'one utterance split into several confirmed segments (mid-utterance cut or boundary-word dup).',
  },
  hijack: {
    brick: '@vexa/gmeet-pipeline (gmeet-channel-binder — active-speaker flicker debounce)',
    why: 'a known silent/noise bot label reached the transcript — a flicker false-positive.',
  },
};

const REPLAY = 'pnpm --filter @vexa/bot run replay   # gate:replay — deterministic offline repro through the REAL gmeet pipeline (no meeting/STT/server)';

function attributeEmpty() {
  console.log('ATTRIBUTION — the bot produced 0 confirmed segments → fault is UPSTREAM of attribution.\n');
  const tree = [
    ['never reached `active` (status: joining / awaiting_admission / failed)',
      '@vexa/join (+ @vexa/remote-browser)',
      'the bot could not join/seat. Check lifecycle failure_stage + `docker logs <bot>`; reproduce join with the join debug harness (modules/join).'],
    ['reached `active` but SILENT (no audio frames)',
      'capture-bridge / @vexa/gmeet-capture',
      'the page-side capture never delivered PCM. Check `[capture]` logs; run `eval/src/capture.mjs` on a recorded tape for ch0/ch1000 health.'],
    ['`active` + audio present but no TEXT',
      '@vexa/transcribe-whisper',
      'frames arrived but STT emitted nothing. Check the transcription service URL/token + `eval benchmark <tape>` (full-audio recall).'],
  ];
  // Point at the likely branch(es) from the last lifecycle status: pre-active → join;
  // active → capture or STT (can't split without more signal, so flag both).
  const hot = !STATUS ? new Set()
    : STATUS === 'active' ? new Set([1, 2]) : new Set([0]);
  for (let i = 0; i < tree.length; i++) {
    const [cond, brick, how] = tree[i];
    const mark = hot.has(i) ? '►' : ' ';
    console.log(` ${mark} ${cond}\n     → ${brick}\n       ${how}`);
  }
  if (STATUS) console.log(`\n  (observed last lifecycle status: ${STATUS})`);
  console.log('\n  Tip: re-run with telemetry capture on to store the raw signal as captured-signal.v1, then');
  console.log('  reproduce offline:  ' + REPLAY);
}

function attributeFlags(flags) {
  if (!flags.length) return attributeEmpty();
  console.log(`ATTRIBUTION — ${flags.length} flagged-issue.v1 record(s):\n`);
  const byType = {};
  for (const f of flags) (byType[f.issue_type] ||= []).push(f);
  let signalLink = null;
  for (const [type, items] of Object.entries(byType)) {
    const map = BRICK[type] || { brick: '(unmapped issue_type)', why: type };
    const ex = items[0];
    console.log(`■ ${type} ×${items.length}  → ${map.brick}`);
    console.log(`    why: ${map.why}`);
    if (ex) console.log(`    e.g. [${ex.speaker}] "${(ex.text || '').slice(0, 60)}"  (seg ${ex.segment_id}${ex.ground_truth ? `, truth="${ex.ground_truth}"` : ''})`);
    const sig = items.map((i) => i.signal).find(Boolean);
    if (sig) signalLink = sig.captured_signal || sig.tape;
    console.log('');
  }
  console.log('Reproduce OFFLINE (separates the brick from the live meeting):');
  console.log('  ' + REPLAY);
  if (signalLink) console.log(`  node meetings/eval/src/replay.mjs ${signalLink}   # live-twin: re-send THIS run's captured signal into a desktop ingest`);
}

if (EMPTY || (!file && STATUS)) { attributeEmpty(); process.exit(0); }
if (!file) { console.error('usage: attribute.mjs <flags.json>  |  attribute.mjs --empty --status <lifecycle-status>'); process.exit(2); }

let parsed;
try { parsed = JSON.parse(readFileSync(file, 'utf8')); }
catch (e) { console.error(`[attribute] ${file} unreadable: ${e.message}`); process.exit(2); }

if (Array.isArray(parsed)) attributeFlags(parsed);
else if (parsed && Array.isArray(parsed.segments)) {
  console.log('[attribute] that looks like a transcript, not flags. Run the verdict first to emit flagged-issue.v1:');
  console.log('  node verdict.mjs <transcript.json> /tmp/flags.json   # then: node attribute.mjs /tmp/flags.json');
} else {
  console.error('[attribute] expected a flagged-issue.v1 array.'); process.exit(2);
}
