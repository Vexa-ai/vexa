/** A real slow HTTP peer is aborted at the configured deadline; retries remain configurable. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { TranscriptionClient } from './index.js';

async function run() {
  let requests = 0;
  const server = createServer((req, res) => {
    requests++;
    req.resume();
    const timer = setTimeout(() => res.end(JSON.stringify({ text: 'ok', segments: [] })), 1500);
    res.on('close', () => clearTimeout(timer));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  assert(address && typeof address !== 'string');
  const serviceUrl = `http://127.0.0.1:${address.port}`;
  const pcm = new Float32Array(1600).fill(0.05);
  try {
    const client = new TranscriptionClient({ serviceUrl, requestTimeoutMs: 1000, maxRetries: 0 });
    const start = performance.now();
    await assert.rejects(() => client.transcribe(pcm), /timeout|abort/i);
    const elapsed = performance.now() - start;
    assert(elapsed >= 900 && elapsed < 1450, `deadline observed at ${elapsed} ms`);
    assert.equal(requests, 1);
    const defaultClient = new TranscriptionClient({ serviceUrl, maxRetries: 0 });
    assert.equal((await defaultClient.transcribe(pcm)).text, 'ok');
    assert.equal(requests, 2);
    const retryClient = new TranscriptionClient({ serviceUrl, requestTimeoutMs: 50, maxRetries: 1, retryDelayMs: 20 });
    await assert.rejects(() => retryClient.transcribe(pcm), /timeout|abort/i);
    assert.equal(requests, 4);
    console.log('PASS configured timeout aborts slow HTTP peer; default and retry options preserved');
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
