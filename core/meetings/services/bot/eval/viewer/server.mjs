#!/usr/bin/env node
/**
 * viewer/server.mjs — the minimal live eyeball for a STANDALONE bot run.
 *
 * A DUMB, transport-agnostic presentation sink: it holds an in-memory view of one run
 * (lifecycle.v1 timeline + transcript.v1 segments + the final verdict) and streams it to the
 * browser over SSE. It knows NOTHING about redis / ssh / bbb — every datum arrives as an HTTP
 * POST, so the same viewer serves a bot on a remote VM (fed by `feed.mjs` over ssh) OR a
 * bot on the local docker network (point its `meetingApiCallbackUrl` straight at `/lifecycle`).
 * That separation keeps it offline-testable (curl in, watch the page) and reusable.
 *
 * Ingress (producers POST here):
 *   POST /lifecycle   one lifecycle.v1 event   { status, reason?, completion_reason?, failure_stage?, ts? }
 *   POST /transcript  one transcript.v1 segment { segment_id, speaker, text, start, end, completed, ... }
 *                     (also accepts the redis mutable envelope { type:'transcript', meeting, segment })
 *   POST /verdict     the final verdict         { pass, line, metrics? }
 * Egress (the browser consumes):
 *   GET  /            the single-page UI (index.html)
 *   GET  /feed        SSE stream — a `snapshot` on connect, then `lifecycle`/`transcript`/`verdict` deltas
 *   GET  /healthz     200 ok
 *
 * No deps, no build, no auth, no DB. Node built-in http only.  Run:  PORT=8090 node viewer/server.mjs
 */
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8090);
const INDEX = join(HERE, 'index.html');

// ── the in-memory view of ONE run ──
const state = {
  lifecycle: [],            // [{ status, reason?, completion_reason?, failure_stage?, ts }]
  segments: new Map(),      // segment_id → transcript.v1 segment (upsert: pending → completed)
  verdict: null,            // { pass, line, metrics? } | null
  startedAt: Date.now(),
};
const clients = new Set();  // open SSE responses

const sseSend = (res, event) => {
  try { res.write(`data: ${JSON.stringify(event)}\n\n`); } catch { /* client gone — reaped on close */ }
};
const broadcast = (event) => { for (const res of clients) sseSend(res, event); };

/** Normalize a transcript POST body to a transcript.v1 segment (accepts the bare segment OR the
 *  redis mutable envelope `{ type:'transcript', meeting, segment }`). Returns null if unusable. */
function segmentOf(body) {
  const seg = body && body.segment ? body.segment : body;
  if (!seg || typeof seg.text !== 'string') return null;
  // Stable identity for upsert/dedup: prefer segment_id, else (speaker,start,end).
  const id = seg.segment_id ?? `${seg.speaker ?? '?'}:${seg.start ?? 0}:${seg.end ?? 0}`;
  return { ...seg, segment_id: String(id) };
}

const readBody = (req) => new Promise((resolve) => {
  let raw = '';
  req.on('data', (c) => { raw += c; if (raw.length > 4_000_000) req.destroy(); });
  req.on('end', () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch { resolve(null); } });
  req.on('error', () => resolve(null));
});

const json = (res, code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };

const server = createServer(async (req, res) => {
  const url = (req.url || '/').split('?')[0];

  if (req.method === 'GET' && (url === '/' || url === '/index.html')) {
    let html;
    try { html = readFileSync(INDEX); } catch { return json(res, 500, { error: 'index.html missing' }); }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    return res.end(html);
  }

  if (req.method === 'GET' && url === '/healthz') return json(res, 200, { ok: true });

  if (req.method === 'GET' && url === '/feed') {
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    res.write('retry: 2000\n\n');
    // Bring a freshly-opened browser fully up to date.
    sseSend(res, { kind: 'snapshot', lifecycle: state.lifecycle, segments: [...state.segments.values()], verdict: state.verdict });
    clients.add(res);
    const ping = setInterval(() => { try { res.write(': ping\n\n'); } catch { /* */ } }, 15000);
    req.on('close', () => { clearInterval(ping); clients.delete(res); });
    return;
  }

  if (req.method === 'POST') {
    const body = await readBody(req);
    if (body === null) return json(res, 400, { error: 'bad json' });

    if (url === '/lifecycle') {
      const ev = { ts: Date.now(), status: body.status ?? 'unknown', reason: body.reason, completion_reason: body.completion_reason, failure_stage: body.failure_stage };
      state.lifecycle.push(ev);
      broadcast({ kind: 'lifecycle', event: ev });
      return json(res, 200, { ok: true });
    }
    if (url === '/transcript') {
      const seg = segmentOf(body);
      if (!seg) return json(res, 400, { error: 'no segment' });
      state.segments.set(seg.segment_id, seg);
      broadcast({ kind: 'transcript', segment: seg });
      return json(res, 200, { ok: true });
    }
    if (url === '/verdict') {
      state.verdict = { pass: !!body.pass, line: body.line ?? '', metrics: body.metrics ?? null };
      broadcast({ kind: 'verdict', verdict: state.verdict });
      return json(res, 200, { ok: true });
    }
    if (url === '/reset') {
      state.lifecycle = []; state.segments.clear(); state.verdict = null; state.startedAt = Date.now();
      broadcast({ kind: 'snapshot', lifecycle: [], segments: [], verdict: null });
      return json(res, 200, { ok: true });
    }
    return json(res, 404, { error: 'no such endpoint' });
  }

  return json(res, 404, { error: 'not found' });
});

server.listen(PORT, () => {
  console.log(`[viewer] live on http://localhost:${PORT}  (SSE /feed · POST /lifecycle /transcript /verdict)`);
});
