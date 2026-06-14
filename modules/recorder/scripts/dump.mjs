/**
 * dump — promote a retained production meeting into a replayable fixture.
 *
 * Retention (CAPTURE_RETENTION=1 on the ingest path) keeps a faithful
 * stream.capture per meeting for a rolling window. This CLI pulls one out into
 * the fixture store so `mixed-replay` / `attribute-fixture` / `npm run e2e` can
 * reproduce a real prod meeting with no live session.
 *
 *   node scripts/dump.mjs list                 # what's retained
 *   node scripts/dump.mjs <name-or-substring>  # → $VEXA_FIXTURE_CACHE/capture/v1/<name>/
 *   node scripts/dump.mjs <query> --out DIR    # → a chosen dir
 *   node scripts/dump.mjs sweep [--days N]     # prune the window now
 *
 * Build first (imports the brick's dist): npm run build
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { listRetention, findRetained, sweepRetention, retentionRoot } from '../dist/index.js';

const argv = process.argv.slice(2);
const cmd = argv[0];
const flag = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };

const fixtureStore = () =>
  process.env.VEXA_FIXTURE_CACHE || path.join(os.homedir(), '.vexa', 'fixtures');

if (!cmd || cmd === 'help' || cmd === '--help') {
  console.log('usage: dump <name|substring> [--out DIR] | dump list | dump sweep [--days N]');
  process.exit(cmd ? 0 : 2);
}

if (cmd === 'list') {
  const all = listRetention();
  if (!all.length) { console.log(`(nothing retained under ${retentionRoot()})`); process.exit(0); }
  for (const m of all) console.log(`${m.day}  ${(m.bytes / 1e6).toFixed(1).padStart(7)}MB  ${m.name}`);
  process.exit(0);
}

if (cmd === 'sweep') {
  const days = flag('--days') ? Number(flag('--days')) : undefined;
  const { kept, removed } = sweepRetention(days);
  console.log(`swept ${retentionRoot()}: removed ${removed.length} day(s) [${removed.join(', ')}], kept ${kept.length}`);
  process.exit(0);
}

// default: dump a meeting by name/substring
const m = findRetained(cmd);
if (!m) { console.error(`no retained meeting matching "${cmd}". try: dump list`); process.exit(1); }
const src = m.dir;
const cap = path.join(src, 'stream.capture');
if (!fs.existsSync(cap)) { console.error(`"${m.name}" has no stream.capture (in-flight or empty)`); process.exit(1); }

const out = flag('--out') || path.join(fixtureStore(), 'capture', 'v1', m.name);
fs.mkdirSync(out, { recursive: true });
fs.copyFileSync(cap, path.join(out, 'stream.capture'));
const meta = path.join(src, 'meta.json');
if (fs.existsSync(meta)) fs.copyFileSync(meta, path.join(out, 'meta.json'));
console.log(`dumped ${m.name} (${(m.bytes / 1e6).toFixed(1)}MB) → ${out}`);
console.log(`replay:  cd ../pipeline && npm run replay:mixed -- ${out}`);
