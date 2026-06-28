#!/usr/bin/env node
// serve — open an eval page over http://localhost (file:// blocks media playback).
//   node serve.mjs --id <id> [--file eval-<a>-<b>.html] [--port 8777]
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = process.env.EVAL_FIXTURES || path.join(HERE, 'fixtures');
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i >= 0 ? process.argv[i + 1] : d; };
const id = arg('id'); const port = Number(arg('port', '8777'));
if (!id) { console.error('usage: node serve.mjs --id <id> [--file <page.html>] [--port 8777]'); process.exit(2); }
const root = path.join(FIXTURES, id);
let file = arg('file');
if (!file) { file = fs.readdirSync(root).filter((f) => f.startsWith('eval-') && f.endsWith('.html')).sort().pop(); }
if (!file) { console.error(`no eval-*.html in ${root} — run.ts first`); process.exit(1); }

const MIME = { '.html': 'text/html', '.wav': 'audio/wav', '.json': 'application/json' };
http.createServer((req, res) => {
  const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || file;
  const p = path.join(root, rel);
  if (!p.startsWith(root) || !fs.existsSync(p)) { res.writeHead(404); res.end('not found'); return; }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
}).listen(port, () => {
  const url = `http://localhost:${port}/${encodeURIComponent(file)}`;
  console.log(`[serve] ${url}\n[serve] opening browser… (Ctrl-C to stop)`);
  execFile('open', [url], () => {});
});
