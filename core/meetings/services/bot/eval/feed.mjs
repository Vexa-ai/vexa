#!/usr/bin/env node
/**
 * feed.mjs — the bbb→viewer bridge. Owns ALL the remote/redis plumbing so the viewer stays a dumb
 * SSE sink. Two producers, both POSTing to the local viewer:
 *
 *   • LIFECYCLE — streams `docker logs -f <bot>` (over ssh) and parses the console lifecycle sink's
 *     `[bot] lifecycle.v1 <status> [(<completion_reason>)] [@<failure_stage>]` lines → POST /lifecycle.
 *     (This is why the bot is launched WITHOUT meetingApiCallbackUrl: the console log IS the source,
 *     so the remote bot needs no inbound reachability. A same-network bot can POST /lifecycle itself.)
 *   • TRANSCRIPT — every POLL_S, dumps the `transcription_segments` redis stream since SINCE and pipes
 *     it through the REUSED `read-redis-transcript.mjs` parser → `{segments}`; dedups; POSTs new ones
 *     → POST /transcript. Same stream + parser the end-of-run scoring uses, so the live view and the
 *     verdict can never diverge.
 *
 * Remote (default, BBB_HOST set):  ssh <BBB_HOST> docker exec/logs …
 * Local  (BBB_HOST unset):         docker exec/logs … on the local daemon
 *
 * env: VIEWER(http://localhost:8090) · BBB_HOST · BOT_CONTAINER · REDIS_CONTAINER(vexa-redis-1)
 *      SINCE(ms) · POLL_S(2)
 */
import { spawn, spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const READ_REDIS = join(HERE, '..', '..', '..', 'eval', 'src', 'read-redis-transcript.mjs');

const VIEWER = (process.env.VIEWER || 'http://localhost:8090').replace(/\/+$/, '');
const BBB = process.env.BBB_HOST || '';
const BOT = process.env.BOT_CONTAINER || 'bot-eval';
const REDIS = process.env.REDIS_CONTAINER || 'vexa-redis-1';
const SINCE = process.env.SINCE || '0';
const POLL_MS = Number(process.env.POLL_S || 2) * 1000;

// Build a shell command that runs `inner` either on bbb (ssh) or locally.
const remote = (inner) => BBB ? `ssh ${BBB} ${JSON.stringify(inner)}` : inner;

async function post(path, body) {
  try {
    await fetch(`${VIEWER}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { console.error(`[feed] POST ${path} failed: ${e.message}`); }
}

// ── LIFECYCLE: stream docker logs, parse the console-sink lines ──
const LIFE_RE = /\[bot\] lifecycle\.v1 (\w+)(?: \(([^)]+)\))?(?: @(\w+))?/;
const seenLife = new Set();
function streamLifecycle() {
  const cmd = remote(`docker logs -f --tail 200 ${BOT}`);
  const child = spawn('bash', ['-c', cmd], { stdio: ['ignore', 'pipe', 'pipe'] });
  let buf = '';
  const onData = (chunk) => {
    buf += chunk.toString();
    const lines = buf.split('\n'); buf = lines.pop() || '';
    for (const line of lines) {
      const m = line.match(LIFE_RE);
      if (!m) continue;
      const key = line.trim();
      if (seenLife.has(key)) continue; seenLife.add(key);
      const [, status, completion_reason, failure_stage] = m;
      console.log(`[feed] lifecycle → ${status}${completion_reason ? ` (${completion_reason})` : ''}${failure_stage ? ` @${failure_stage}` : ''}`);
      post('/lifecycle', { status, completion_reason, failure_stage });
    }
  };
  child.stdout.on('data', onData);
  child.stderr.on('data', onData);
  child.on('exit', (code) => { console.log(`[feed] docker logs exited (${code}); retrying in 3s`); setTimeout(streamLifecycle, 3000); });
}

// ── TRANSCRIPT: poll the stream, parse via read-redis-transcript.mjs, POST new segments ──
const seenSeg = new Set();
function pollTranscript() {
  const dump = remote(`docker exec ${REDIS} redis-cli XRANGE transcription_segments ${SINCE} +`);
  const r = spawnSync('bash', ['-c', `${dump} | node ${JSON.stringify(READ_REDIS)}`], { encoding: 'utf8' });
  if (r.status !== 0) { console.error(`[feed] transcript poll failed: ${(r.stderr || '').trim().slice(0, 160)}`); return; }
  let segs = [];
  try { segs = (JSON.parse(r.stdout || '{}').segments) || []; } catch { /* partial/empty */ }
  for (const s of segs) {
    const key = s.segment_id || `${s.start}|${s.end}|${s.text}`;
    if (seenSeg.has(key)) continue; seenSeg.add(key);
    post('/transcript', s);
  }
  if (segs.length) console.log(`[feed] transcript: ${seenSeg.size} segment(s) seen`);
}

console.log(`[feed] bridging ${BBB ? `ssh ${BBB} ` : '(local) '}${BOT}/${REDIS} → ${VIEWER}  (since ${SINCE}, poll ${POLL_MS}ms)`);
streamLifecycle();
pollTranscript();
setInterval(pollTranscript, POLL_MS);
