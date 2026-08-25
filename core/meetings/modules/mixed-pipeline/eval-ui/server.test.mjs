import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTeamsCsrcEvalServer } from './server.mjs';

const root = mkdtempSync(join(tmpdir(), 'teams-csrc-eval-ui-'));
const outside = mkdtempSync(join(tmpdir(), 'teams-csrc-eval-ui-outside-'));
const bytes = Buffer.from([...Array(32).keys()]);
writeFileSync(join(root, 'audio.wav'), bytes);
writeFileSync(join(root, 'result.json'), '{"kind":"fixture"}\n');
writeFileSync(join(outside, 'private.json'), '{"private":true}\n');
symlinkSync(join(outside, 'private.json'), join(root, 'escape.json'));
const server = createTeamsCsrcEvalServer({ root });

try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  assert.equal(typeof address, 'object');
  const base = `http://127.0.0.1:${address.port}`;

  const index = await fetch(`${base}/`);
  assert.equal(index.status, 200);
  assert.match(await index.text(), /audio-aligned transcript replay/);

  const module = await fetch(`${base}/_viewer/teams-csrc-timeline.mjs`);
  assert.equal(module.status, 200);
  assert.match(module.headers.get('content-type'), /text\/javascript/);

  const live = await fetch(`${base}/live.html`);
  assert.equal(live.status, 200);
  assert.match(await live.text(), /Live call replay/);

  const liveModule = await fetch(`${base}/_viewer/teams-csrc-live-transcript.mjs`);
  assert.equal(liveModule.status, 200);
  assert.match(await liveModule.text(), /createTranscriptManager/);

  const sharedRenderer = await fetch(`${base}/_shared/transcript-rendering.js`);
  assert.equal(sharedRenderer.status, 200);
  assert.match(await sharedRenderer.text(), /createTranscriptManager/);

  const range = await fetch(`${base}/audio.wav`, { headers: { range: 'bytes=4-7' } });
  assert.equal(range.status, 206);
  assert.equal(range.headers.get('content-range'), 'bytes 4-7/32');
  assert.deepEqual([...new Uint8Array(await range.arrayBuffer())], [4, 5, 6, 7]);

  const suffix = await fetch(`${base}/audio.wav`, { headers: { range: 'bytes=-3' } });
  assert.equal(suffix.status, 206);
  assert.deepEqual([...new Uint8Array(await suffix.arrayBuffer())], [29, 30, 31]);

  const invalid = await fetch(`${base}/audio.wav`, { headers: { range: 'bytes=99-100' } });
  assert.equal(invalid.status, 416);

  const data = await fetch(`${base}/result.json`);
  assert.equal(data.status, 200);
  assert.match(data.headers.get('content-type'), /application\/json/);

  const escape = await fetch(`${base}/escape.json`);
  assert.equal(escape.status, 403);
} finally {
  await new Promise((resolve) => server.close(resolve));
  rmSync(root, { recursive: true, force: true });
  rmSync(outside, { recursive: true, force: true });
}

console.log('teams-csrc-eval-ui server: static and byte-range checks passed');
