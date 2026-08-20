#!/usr/bin/env node
import { createReadStream, existsSync, realpathSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, isAbsolute, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHARED_TRANSCRIPT_RENDERER = resolve(HERE, '../../../../../packages/transcript-rendering/dist/index.js');
const CONTENT_TYPES = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.wav', 'audio/wav'],
  ['.mp3', 'audio/mpeg'],
  ['.flac', 'audio/flac'],
]);
const VIEWER_MODULES = new Set([
  'teams-csrc-timeline.mjs',
  'teams-contested-word-detector.mjs',
  'teams-csrc-live-transcript.mjs',
  'teams-csrc-live-model.mjs',
  'teams-csrc-pipeline-live.mjs',
]);

const argument = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
};

const sendJson = (res, status, body) => {
  const bytes = Buffer.from(`${JSON.stringify(body)}\n`);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': bytes.length });
  res.end(bytes);
};

function boundedPath(root, pathname) {
  let decoded;
  try { decoded = decodeURIComponent(pathname); } catch { return null; }
  if (decoded.includes('\0')) return null;
  const relative = decoded.replace(/^\/+/, '');
  const candidate = resolve(root, relative);
  if (candidate !== root && !candidate.startsWith(`${root}${sep}`)) return null;
  if (!existsSync(candidate)) return candidate;
  const real = realpathSync(candidate);
  return real === root || real.startsWith(`${root}${sep}`) ? real : null;
}

function parseRange(header, size) {
  if (!header) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match) return { invalid: true };
  if (!match[1] && !match[2]) return { invalid: true };
  let start;
  let end;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isInteger(suffix) || suffix <= 0) return { invalid: true };
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(match[1]);
    end = match[2] ? Number(match[2]) : size - 1;
  }
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= size || end < start) {
    return { invalid: true };
  }
  return { start, end: Math.min(end, size - 1) };
}

function sendFile(req, res, path) {
  if (!existsSync(path)) return sendJson(res, 404, { error: 'not found' });
  const stat = statSync(path);
  if (!stat.isFile()) return sendJson(res, 404, { error: 'not found' });
  const range = parseRange(req.headers.range, stat.size);
  if (range?.invalid) {
    res.writeHead(416, { 'content-range': `bytes */${stat.size}`, 'accept-ranges': 'bytes' });
    return res.end();
  }
  const contentType = CONTENT_TYPES.get(extname(path).toLowerCase()) ?? 'application/octet-stream';
  const headers = { 'content-type': contentType, 'accept-ranges': 'bytes', 'cache-control': 'no-store' };
  if (range) {
    const length = range.end - range.start + 1;
    res.writeHead(206, { ...headers, 'content-length': length, 'content-range': `bytes ${range.start}-${range.end}/${stat.size}` });
    if (req.method === 'HEAD') return res.end();
    return createReadStream(path, { start: range.start, end: range.end }).pipe(res);
  }
  res.writeHead(200, { ...headers, 'content-length': stat.size });
  if (req.method === 'HEAD') return res.end();
  return createReadStream(path).pipe(res);
}

export function createTeamsCsrcEvalServer({
  root,
  viewerDir = HERE,
  transcriptRendererPath = SHARED_TRANSCRIPT_RENDERER,
} = {}) {
  if (!root || !isAbsolute(root)) throw new Error('root must be an absolute path');
  const resolvedRoot = realpathSync(resolve(root));
  const resolvedViewer = realpathSync(resolve(viewerDir));
  const resolvedTranscriptRenderer = realpathSync(resolve(transcriptRendererPath));
  return createServer((req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { error: 'method not allowed' });
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    if (url.pathname === '/' || url.pathname === '/index.html') return sendFile(req, res, resolve(resolvedViewer, 'index.html'));
    if (url.pathname === '/live.html') return sendFile(req, res, resolve(resolvedViewer, 'live.html'));
    if (url.pathname === '/_shared/transcript-rendering.js') return sendFile(req, res, resolvedTranscriptRenderer);
    if (url.pathname.startsWith('/_viewer/')) {
      const moduleName = url.pathname.slice('/_viewer/'.length);
      if (!VIEWER_MODULES.has(moduleName)) return sendJson(res, 404, { error: 'viewer module not found' });
      return sendFile(req, res, resolve(resolvedViewer, moduleName));
    }
    const path = boundedPath(resolvedRoot, url.pathname);
    if (!path) return sendJson(res, 403, { error: 'outside fixture root' });
    return sendFile(req, res, path);
  });
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const root = resolve(argument('root', ''));
  const port = Number(argument('port', '8767'));
  if (!argument('root')) throw new Error('--root /absolute/fixture/output/directory is required');
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('--port must be 0..65535');
  const server = createTeamsCsrcEvalServer({ root });
  server.listen(port, '127.0.0.1', () => {
    const address = server.address();
    const actualPort = typeof address === 'object' && address ? address.port : port;
    console.log(`[teams-csrc-eval-ui] http://127.0.0.1:${actualPort}/?data=/result.json&audio=/audio.wav`);
    console.log(`[teams-csrc-eval-ui] fixture root ${root}`);
  });
}
