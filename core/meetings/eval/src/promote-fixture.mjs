#!/usr/bin/env node
// promote-fixture — a witnessed session becomes a permanent regression fixture.
//
// The framework's contract is "every witnessed session joins the regression corpus" (FRAMEWORK.md).
// A session file alone cannot honour it: replaying audio proves nothing unless the numbers it USED
// to produce are recorded beside it. A fixture is therefore an entry, not a file —
//
//   $VEXA_CORPUS/<platform>/<slug>/
//     session.captured-signal.jsonl.gz   the signal, exactly as captured
//     baseline.json                      every metric at promotion time — the regression contract
//     manifest.json                      where it came from, at which commit, cut to which window
//     reference.txt                      the single-pass ground truth (optional; costs STT once)
//     transcript.json                    the live transcript that reference was scored against
//
// — and the audio stays OUT of the repo (real speech is sensitive; sessions run to hundreds of MB),
// while `eval/CORPUS.md` carries the index so a fresh checkout can still find and score it.
//
//   node src/promote-fixture.mjs <tape.jsonl | session.captured-signal.jsonl[.gz]> --slug <slug> \
//     [--platform p] [--head-sec n] [--reference ref.txt] [--transcript t.json|url] [--note "…"] \
//     [--stt-service url] [--stt-model m]     (default: the TRANSCRIPTION_* env the run used)
//
// Corpus root: $VEXA_CORPUS (default ~/vexa-test-rig/fixtures).
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { homedir } from 'node:os';
import path from 'node:path';
import { tapeToSignal } from './tape-to-signal.mjs';
import { loadSession, sessionMetrics, formatMetrics } from './signal-metrics.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
export const CORPUS = process.env.VEXA_CORPUS || path.join(homedir(), 'vexa-test-rig', 'fixtures');

const argv = process.argv.slice(2);
const flags = {};
const positional = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i].startsWith('--')) flags[argv[i].slice(2)] = argv[++i];
  else positional.push(argv[i]);
}
const flag = (name, dflt) => flags[name] ?? dflt;
const input = positional[0];

if (!input || !flag('slug')) {
  console.error('usage: promote-fixture.mjs <tape.jsonl|session.captured-signal.jsonl[.gz]> --slug <slug> [--platform p] [--head-sec n] [--reference f] [--transcript f|url] [--note "…"] [--stt-service url] [--stt-model m]');
  process.exit(1);
}

const slug = flag('slug');
const headSec = Number(flag('head-sec', Infinity));
const note = flag('note', '');
const refPath = flag('reference');
const txPath = flag('transcript');

// A tape is the raw ingest stream; a session is already captured-signal.v1. Both are legitimate
// sources — the desktop produces the first, a bot's recorder the second — so accept either and
// converge here.
const isTape = !input.includes('captured-signal');
let header, records;
if (isTape) {
  ({ header, records } = tapeToSignal(input, { headSec }));
} else {
  const s = loadSession(input);
  header = s.header;
  const all = [...s.frames, ...s.hints, ...s.cuts].sort((a, b) => (a.ts ?? a.t ?? a.tMs) - (b.ts ?? b.t ?? b.tMs));
  const first = s.frames.length ? Math.min(...s.frames.map((f) => f.ts)) : 0;
  records = Number.isFinite(headSec) ? all.filter((r) => (r.ts ?? r.t ?? r.tMs) <= first + headSec * 1000) : all;
}

const platform = flag('platform', header.platform ?? 'unknown');
const dir = path.join(CORPUS, platform, slug);
mkdirSync(dir, { recursive: true });

const body = [header, ...records].map((r) => JSON.stringify(r)).join('\n') + '\n';
const gz = gzipSync(Buffer.from(body), { level: 9 });
const sessionFile = path.join(dir, 'session.captured-signal.jsonl.gz');
writeFileSync(sessionFile, gz);

const metrics = sessionMetrics({
  header,
  frames: records.filter((r) => r.type !== 'hint' && r.type !== 'boundary'),
  hints: records.filter((r) => r.type === 'hint'),
  cuts: records.filter((r) => r.type === 'boundary'),
});

const gitSha = (() => {
  try { return execFileSync('git', ['-C', HERE, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim(); }
  catch { return null; }
})();

if (refPath) copyFileSync(refPath, path.join(dir, 'reference.txt'));
// A URL is fetched, not linked: the desktop store keeps growing after the session ends, so the
// transcript must be pinned at promotion time or the baseline drifts under its own fixture.
if (txPath) {
  if (txPath.startsWith('http')) {
    const r = await fetch(txPath);
    if (!r.ok) throw new Error(`transcript fetch ${r.status} from ${txPath}`);
    writeFileSync(path.join(dir, 'transcript.json'), JSON.stringify(await r.json(), null, 2));
  } else {
    copyFileSync(txPath, path.join(dir, 'transcript.json'));
  }
}

// Content scoring is a pure text comparison once the reference exists, so it needs no STT account —
// and it is the SAME scorer that produced the reference, never a second implementation.
let content = null;
if (refPath && txPath) {
  const tmp = path.join(dir, '.score.tmp.json');
  execFileSync('python3', [
    path.join(HERE, 'single_pass_truth.py'), path.join(dir, 'session.captured-signal.jsonl.gz'),
    '--reference', path.join(dir, 'reference.txt'),
    '--realtime', path.join(dir, 'transcript.json'),
    '--json', tmp,
  ], { stdio: 'inherit' });
  content = JSON.parse(readFileSync(tmp, 'utf8'));
  execFileSync('rm', ['-f', tmp]);
}

const manifest = {
  slug,
  platform,
  lane: header.lane ?? (platform === 'google_meet' ? 'gmeet' : 'mixed'),
  language: header.language ?? null,
  nativeMeetingId: header.native_meeting_id ?? null,
  source: header.source ?? 'bot-recorder',
  sourceFile: path.basename(input),
  sourceBytes: statSync(input).size,
  headSec: Number.isFinite(headSec) ? headSec : null,
  // ONE pair for the whole entry, because the comparison only means anything if the live transcript
  // and the single-pass reference came from the same STT — that identity is what separates "the
  // model cannot hear this" from "our streaming threw it away".
  stt: {
    service: flag('stt-service', process.env.TRANSCRIPTION_SERVICE_URL ?? null),
    model: flag('stt-model', process.env.TRANSCRIPTION_MODEL ?? null),
  },
  promotedAt: new Date().toISOString(),
  gitSha,
  note,
  sessionSha256: createHash('sha256').update(gz).digest('hex'),
  files: {
    session: 'session.captured-signal.jsonl.gz',
    ...(refPath ? { reference: 'reference.txt' } : {}),
    ...(txPath ? { transcript: 'transcript.json' } : {}),
  },
};
writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
writeFileSync(path.join(dir, 'baseline.json'), JSON.stringify({
  recordedAt: manifest.promotedAt, gitSha, metrics, ...(content ? { content } : {}),
}, null, 2) + '\n');

console.log(`${platform}/${slug} → ${dir}`);
console.log(formatMetrics(metrics));
if (content) console.log(`  content   recall ${content.recall} · precision ${content.precision} (vs single-pass reference)`);
console.log(`  ${(gz.length / 1e6).toFixed(1)} MB gzipped · sha256 ${manifest.sessionSha256.slice(0, 12)}…`);
