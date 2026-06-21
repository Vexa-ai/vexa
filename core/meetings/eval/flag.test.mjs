#!/usr/bin/env node
/**
 * O-TEL-3 — flag-a-bug → store → surface → replay-routing (flagged-issue.v1). OFFLINE, no meeting.
 *
 * Exercises the whole loop on GOLDENS / a planted fixture, asserting:
 *   • flag → store → surface — a flagged issue (human AND system) lands in the store and is
 *     SURFACED on the system queue (open/investigating bubble up);
 *   • analyze.mjs --flag-issues on a fixture with a PLANTED mis-attribution emits a conforming
 *     flagged-issue.v1 record (the auto-flagger, derived from analyze.mjs's own oracle);
 *   • the emitted record is valid against the PUBLISHED flagged-issue.v1 schema (ajv vs SSOT);
 *   • routeToReplay resolves the issue's signal link → a REPLAYABLE captured-signal.v1 (the file
 *     exists, parses as a captured-signal session, and yields the O-TEL-2 replay command);
 *   • the committed flagged-issue.v1 goldens conform.
 *
 * Run: node eval/flag.test.mjs   (from meetings/) — zero-npm-dep (ajv hoisted at the repo root).
 */
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync, existsSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FlagStore } from './src/flag-store.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SCHEMA = join(HERE, '..', 'contracts', 'flagged-issue.v1', 'flagged-issue.schema.json');
const GOLDEN = join(HERE, '..', 'contracts', 'flagged-issue.v1', 'golden');
const ANALYZE = join(HERE, 'src', 'analyze.mjs');
const FIXTURE_TX = join(HERE, 'replay-fixture', 'transcript-misattr.json');
const FIXTURE_SIGNAL = join(HERE, 'replay-fixture', 'session.captured-signal.jsonl');
const FLAG_OUT = join(HERE, 'replay-fixture', '.flag-out.tmp.json');
const SIG_HEADER = join(HERE, '..', 'contracts', 'captured-signal.v1', 'golden', 'SessionHeader.gmeet.json');
// The meeting's distributed trace_id (O-OBS-1) — threaded into the auto-flagger so a flagged bug
// ties to the captured-signal session header carrying the SAME id.
const TRACE = '7f3a1c9e2b8d4a6f5e0c1d2b3a4f5e6d';

let failed = 0;
const check = (name, cond, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── flagged-issue.v1 validator (ajv against the PUBLISHED schema; P8) ──
const schema = JSON.parse(readFileSync(SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(schema);
const validateIssue = ajv.compile({ $ref: `${schema.$id}#/$defs/FlaggedIssue` });

async function main() {
  // ── 1) flag → store → surface (human + system land in the same queue) ──
  {
    const store = new FlagStore(null);   // in-memory
    const human = store.flag({
      platform: 'teams', native_meeting_id: '19:abc', session_start_time: '2026-06-21T11:00:00.000Z',
      segment_id: 'ch-999:3:12000', speaker: 'Vera', text: '[inaudible]', start: 12, end: 13.5,
      issue_type: 'lost-content', severity: 'critical', flagged_by: 'human',
      signal: { captured_signal: 'captures/x.captured-signal.jsonl', frame_seq_start: 60, frame_seq_end: 75 },
    });
    const sys = store.flag({
      platform: 'zoom', native_meeting_id: '123', session_start_time: '2026-06-21T10:00:00.000Z',
      segment_id: 'ch-1:1:4200', speaker: 'spk-anna', text: 'this is Boris', start: 4.2, end: 7.1,
      issue_type: 'mis-attribution', severity: 'high', flagged_by: 'system', status: 'investigating',
      signal: { tape: 'tapes/t.jsonl' },
    });
    check('flag() stamps an issue_id + created_at', !!human.issue_id && !!human.created_at, JSON.stringify(human));
    check('flag() defaults status to open (human)', human.status === 'open', human.status);
    check('flag() honors an explicit status (system: investigating)', sys.status === 'investigating', sys.status);
    check('store.get retrieves a flagged issue', store.get(human.issue_id)?.segment_id === 'ch-999:3:12000');
    const q = store.queue({ open: true });
    check('system queue SURFACES both issues (open + investigating)', q.length === 2, `n=${q.length}`);
    check('queue bubbles open above investigating', q[0].status === 'open' && q[1].status === 'investigating', q.map((i) => i.status).join(','));
    check('both stored issues are flagged-issue.v1-valid (ajv vs SSOT)',
      store.all().every((i) => !!validateIssue(i)), ajv.errorsText(validateIssue.errors));
  }

  // ── 2) analyze.mjs --flag-issues on a PLANTED mis-attribution → conforming flagged-issue.v1 ──
  {
    if (existsSync(FLAG_OUT)) rmSync(FLAG_OUT);
    execFileSync('node', [ANALYZE, 'zoom', 'flag-fixture-001', '--flag-issues', '--flag-out', FLAG_OUT], {
      env: { ...process.env, TRANSCRIPT_FILE: FIXTURE_TX, FLAG_SIGNAL: FIXTURE_SIGNAL, FLAG_TRACE: TRACE },
      stdio: 'pipe',
    });
    const emitted = JSON.parse(readFileSync(FLAG_OUT, 'utf8'));
    check('auto-flagger emitted ≥1 issue for the planted mis-attribution', emitted.length >= 1, `n=${emitted.length}`);
    const ma = emitted.find((i) => i.issue_type === 'mis-attribution');
    check('emitted issue_type derives from analyze.mjs oracle (mis-attribution)', !!ma, JSON.stringify(emitted));
    check('emitted issue is flagged-issue.v1-valid (ajv vs SSOT)', !!ma && !!validateIssue(ma), ajv.errorsText(validateIssue.errors));
    check('emitted issue carries ground_truth + system flag', ma?.ground_truth === 'boris' && ma?.flagged_by === 'system', JSON.stringify(ma));
    check('emitted issue links to the captured-signal.v1 raw signal', ma?.signal?.captured_signal === FIXTURE_SIGNAL, JSON.stringify(ma?.signal));
    check('emitted issue carries the meeting trace_id (O-OBS-1 ↔ O-TEL-3)', ma?.trace_id === TRACE, JSON.stringify(ma?.trace_id));

    // ── 3) routing: the flagged issue → its captured-signal/tape → the O-TEL-2 replay ──
    const store = new FlagStore(null);
    const stored = store.flag(ma);
    const route = store.routeToReplay(stored.issue_id);
    check('routeToReplay resolves the issue → its signal path', !!route && route.signalPath === FIXTURE_SIGNAL, JSON.stringify(route));
    check('routeToReplay yields the live + offline replay commands',
      !!route && /replay\.mjs/.test(route.replayCmd) && /run replay/.test(route.offlineReplayCmd), JSON.stringify(route));
    // The signal link RESOLVES to a real, replayable captured-signal.v1 session.
    check('the linked signal file exists', existsSync(route.signalPath));
    const lines = readFileSync(route.signalPath, 'utf8').split('\n').filter(Boolean);
    const header = JSON.parse(lines[0]);
    check('the linked signal is a replayable captured-signal.v1 session (header + frames)',
      header.type === 'captured_signal_header' && lines.length > 1, `lines=${lines.length}`);
    rmSync(FLAG_OUT, { force: true });
  }

  // ── 4) committed flagged-issue.v1 goldens conform ──
  {
    const { readdirSync } = await import('node:fs');
    const files = readdirSync(GOLDEN).filter((f) => f.endsWith('.json'));
    const allOk = files.every((f) => !!validateIssue(JSON.parse(readFileSync(join(GOLDEN, f), 'utf8'))));
    check(`all ${files.length} flagged-issue.v1 goldens conform`, allOk, ajv.errorsText(validateIssue.errors));
  }

  // ── 5) trace linkage (O-OBS-1 ↔ O-TEL-3): the captured-signal.v1 session header and the
  //     flagged-issue.v1 record can share ONE trace_id — tying the raw signal AND the bug record
  //     to the meeting's full cross-system trace, so "trace bugs precisely + replay and fix" is one
  //     key, not two disconnected loops. Asserted on the committed goldens. ──
  {
    const sigHeader = JSON.parse(readFileSync(SIG_HEADER, 'utf8'));
    const issue = JSON.parse(readFileSync(join(GOLDEN, 'FlaggedIssue.misattr.json'), 'utf8'));
    check('captured-signal.v1 header carries a trace_id', typeof sigHeader.trace_id === 'string' && sigHeader.trace_id.length > 0, JSON.stringify(sigHeader.trace_id));
    check('flagged-issue.v1 carries a trace_id', typeof issue.trace_id === 'string' && issue.trace_id.length > 0, JSON.stringify(issue.trace_id));
    check('the SAME trace_id links the flagged bug to its captured signal (obs ↔ telemetry)', sigHeader.trace_id === issue.trace_id, `${sigHeader.trace_id} vs ${issue.trace_id}`);
  }

  if (failed) { console.error(`\n❌ flag (O-TEL-3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ flag (O-TEL-3): flag→store→surface works (human+system on one queue); analyze.mjs --flag-issues auto-emits a conforming flagged-issue.v1 for a planted mis-attribution; the issue routes to its captured-signal.v1 → the O-TEL-2 replay; goldens conform.');
}

// top-level await for the dynamic import in block 4
await main();
