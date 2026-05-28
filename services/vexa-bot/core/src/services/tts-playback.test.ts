import assert from 'node:assert/strict';
import http from 'node:http';
import type { AddressInfo } from 'node:net';

import { TTSPlaybackService } from './tts-playback';

async function testMissingTtsServiceUrlCleansState(): Promise<void> {
  const previousUrl = process.env.TTS_SERVICE_URL;
  delete process.env.TTS_SERVICE_URL;

  try {
    const service = new TTSPlaybackService();

    await assert.rejects(
      () => service.synthesizeAndPlay('Hello from Vexa', 'piper', 'auto'),
      /TTS_SERVICE_URL not set/
    );
    assert.equal(service.isPlaying(), false);
    assert.equal(service.getCurrentText(), null);
  } finally {
    if (previousUrl === undefined) {
      delete process.env.TTS_SERVICE_URL;
    } else {
      process.env.TTS_SERVICE_URL = previousUrl;
    }
  }
}

async function testDefaultVoiceAutoIsPostedToTtsService(): Promise<void> {
  const previousUrl = process.env.TTS_SERVICE_URL;
  let requestBody = '';

  const server = http.createServer((req, res) => {
    req.setEncoding('utf8');
    req.on('data', (chunk) => {
      requestBody += chunk;
    });
    req.on('end', () => {
      res.statusCode = 503;
      res.end('synthetic unavailable');
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert(address && typeof address === 'object');
  const { port } = address as AddressInfo;

  try {
    process.env.TTS_SERVICE_URL = `http://127.0.0.1:${port}`;
    const service = new TTSPlaybackService();

    await assert.rejects(
      () => service.synthesizeAndPlay('Hola desde Vexa'),
      /TTS service error 503/
    );

    assert.equal(JSON.parse(requestBody).voice, 'auto');
    assert.equal(service.isPlaying(), false);
    assert.equal(service.getCurrentText(), null);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    if (previousUrl === undefined) {
      delete process.env.TTS_SERVICE_URL;
    } else {
      process.env.TTS_SERVICE_URL = previousUrl;
    }
  }
}

async function main(): Promise<void> {
  await testMissingTtsServiceUrlCleansState();
  await testDefaultVoiceAutoIsPostedToTtsService();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
