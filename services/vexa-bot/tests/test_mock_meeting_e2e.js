/**
 * End-to-end test: per-speaker audio → transcription-service → Redis
 *
 * Captures audio from 3 mock meeting speakers, sends each to
 * transcription-service independently, publishes segments to Redis
 * with speaker labels, verifies correct attribution.
 *
 * Prerequisites:
 *   - Mock meeting: python3 -m http.server 8089 (from tests/mock-meeting/)
 *   - transcription-service on localhost:8083
 *   - Redis on localhost:6379 (or tests-redis-1 on 6399)
 *   - Browser with CDP at localhost:9222
 *
 * Run:
 *   node tests/test_mock_meeting_e2e.js
 */

const { chromium } = require('/home/dima/dev/playwright-vnc-poc/node_modules/playwright');
const http = require('http');
const { createClient } = require('redis');

const MOCK_URL = process.env.MOCK_MEETING_URL || 'http://172.17.0.1:8089';
const TRANSCRIPTION_URL = process.env.TRANSCRIPTION_SERVICE_URL || 'http://localhost:8083/v1/audio/transcriptions';
const TRANSCRIPTION_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || 'your_secure_token_here';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6399';
const STREAM_KEY = 'transcription_segments_test';
const SPEAKER_EVENTS_KEY = 'speaker_events_test';

async function transcribeAudio(float32Array, speakerName) {
  // Convert Float32 to Int16 PCM WAV
  const numSamples = float32Array.length;
  const wavBuffer = Buffer.alloc(44 + numSamples * 2);

  // WAV header
  wavBuffer.write('RIFF', 0);
  wavBuffer.writeUInt32LE(36 + numSamples * 2, 4);
  wavBuffer.write('WAVE', 8);
  wavBuffer.write('fmt ', 12);
  wavBuffer.writeUInt32LE(16, 16);
  wavBuffer.writeUInt16LE(1, 20); // PCM
  wavBuffer.writeUInt16LE(1, 22); // mono
  wavBuffer.writeUInt32LE(16000, 24); // sample rate
  wavBuffer.writeUInt32LE(32000, 28); // byte rate
  wavBuffer.writeUInt16LE(2, 32); // block align
  wavBuffer.writeUInt16LE(16, 34); // bits per sample
  wavBuffer.write('data', 36);
  wavBuffer.writeUInt32LE(numSamples * 2, 40);

  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    wavBuffer.writeInt16LE(Math.round(s * 32767), 44 + i * 2);
  }

  // POST to transcription-service using multipart boundary
  const boundary = '----VexaTestBoundary' + Date.now();
  const parts = [];

  // File part
  parts.push(`--${boundary}\r\n`);
  parts.push(`Content-Disposition: form-data; name="file"; filename="${speakerName}.wav"\r\n`);
  parts.push(`Content-Type: audio/wav\r\n\r\n`);
  const headerBuf = Buffer.from(parts.join(''));
  const modelPart = Buffer.from(`\r\n--${boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nlarge-v3-turbo\r\n--${boundary}--\r\n`);
  const body = Buffer.concat([headerBuf, wavBuffer, modelPart]);

  const res = await fetch(TRANSCRIPTION_URL, {
    method: 'POST',
    headers: {
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Authorization': `Bearer ${TRANSCRIPTION_TOKEN}`,
    },
    body: body,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text.substring(0, 200)}`);
  }

  return res.json();
}

(async () => {
  console.log('=== E2E: Per-Speaker Mock Meeting → Transcription → Redis ===\n');

  // Connect to Redis
  const redis = createClient({ url: REDIS_URL });
  await redis.connect();

  // Clean test keys
  await redis.del(STREAM_KEY);
  await redis.del(SPEAKER_EVENTS_KEY);
  console.log('Redis connected, test keys cleaned.\n');

  // Connect to browser
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = await context.newPage();

  try {
    // 1. Open mock meeting and start audio
    console.log('1. Opening mock meeting...');
    await page.goto(MOCK_URL);
    await page.evaluate(() => {
      document.querySelectorAll('audio').forEach(a => { a.loop = false; a.play().catch(() => {}); });
    });
    await page.waitForTimeout(1000);

    // 2. Resolve speaker names
    console.log('2. Resolving speakers...');
    const speakers = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('audio')).map((el, i) => {
        const container = el.closest('.participant');
        const name = container?.querySelector('.participant-name')?.textContent?.trim() || `Speaker ${i}`;
        return { index: i, id: el.id, name, paused: el.paused };
      });
    });
    for (const s of speakers) {
      console.log(`   [${s.index}] ${s.name} (${s.id}, paused=${s.paused})`);
    }

    // 3. Capture per-speaker audio (5 seconds each, simultaneously)
    console.log('\n3. Capturing per-speaker audio (5s)...');
    const audioData = await page.evaluate(async () => {
      const elements = Array.from(document.querySelectorAll('audio')).filter(a => !a.paused);
      const results = [];

      const capturePromises = elements.map((el, i) => new Promise(async (resolve) => {
        try {
          const stream = el.captureStream ? el.captureStream() : el.mozCaptureStream();
          if (!stream || stream.getAudioTracks().length === 0) {
            resolve({ index: i, error: 'No audio tracks' });
            return;
          }

          const ctx = new AudioContext({ sampleRate: 16000 });
          const source = ctx.createMediaStreamSource(stream);
          const processor = ctx.createScriptProcessor(4096, 1, 1);
          const chunks = [];

          processor.onaudioprocess = (e) => {
            const data = e.inputBuffer.getChannelData(0);
            if (data.some(s => Math.abs(s) > 0.001)) {
              chunks.push(Array.from(data));
            }
          };

          source.connect(processor);
          processor.connect(ctx.destination);

          await new Promise(r => setTimeout(r, 5000));

          processor.disconnect();
          source.disconnect();
          await ctx.close();

          // Flatten chunks to one array
          const flat = chunks.flat();
          resolve({ index: i, samples: flat, duration: (flat.length / 16000).toFixed(2) });
        } catch (err) {
          resolve({ index: i, error: err.message });
        }
      }));

      return Promise.all(capturePromises);
    });

    for (const a of audioData) {
      if (a.error) {
        console.log(`   [${a.index}] ERROR: ${a.error}`);
      } else {
        console.log(`   [${a.index}] ${speakers[a.index].name}: ${a.duration}s captured (${a.samples.length} samples)`);
      }
    }

    // 4. Transcribe each speaker independently
    console.log('\n4. Transcribing per speaker...');
    const transcriptions = [];
    for (const a of audioData) {
      if (a.error || a.samples.length < 16000) {
        console.log(`   [${a.index}] Skipped (${a.error || 'too short'})`);
        continue;
      }

      const speaker = speakers[a.index];
      const float32 = new Float32Array(a.samples);

      try {
        const result = await transcribeAudio(float32, speaker.name);
        console.log(`   [${a.index}] ${speaker.name}: "${result.text?.substring(0, 80)}..."`);
        transcriptions.push({ speaker: speaker.name, result });

        // 5. Publish to Redis
        await redis.xAdd(STREAM_KEY, '*', {
          speaker: speaker.name,
          text: result.text || '',
          language: result.language || 'en',
          duration: String(result.duration || 0),
          meeting_id: 'mock-test-001',
        });

        // Speaker event
        await redis.xAdd(SPEAKER_EVENTS_KEY, '*', {
          speaker: speaker.name,
          type: 'segment_produced',
          timestamp: String(Date.now() / 1000),
        });

      } catch (err) {
        console.log(`   [${a.index}] ${speaker.name}: TRANSCRIPTION FAILED — ${err.message}`);
      }
    }

    // 6. Verify Redis
    console.log('\n5. Verifying Redis...');
    const segments = await redis.xRange(STREAM_KEY, '-', '+');
    const events = await redis.xRange(SPEAKER_EVENTS_KEY, '-', '+');

    console.log(`   Segments in Redis: ${segments.length}`);
    console.log(`   Speaker events in Redis: ${events.length}`);

    const speakerSegments = {};
    for (const s of segments) {
      const speaker = s.message.speaker;
      if (!speakerSegments[speaker]) speakerSegments[speaker] = [];
      speakerSegments[speaker].push(s.message.text?.substring(0, 60));
    }

    console.log('\n   Per-speaker segments:');
    for (const [speaker, texts] of Object.entries(speakerSegments)) {
      console.log(`   ${speaker}: "${texts[0]}..."`);
    }

    // 7. Cross-contamination check
    console.log('\n6. Cross-contamination check...');
    const aliceText = speakerSegments['Alice Johnson']?.[0]?.toLowerCase() || '';
    const bobText = speakerSegments['Bob Smith']?.[0]?.toLowerCase() || '';
    const carolText = speakerSegments['Carol Williams']?.[0]?.toLowerCase() || '';

    const aliceHasProduct = aliceText.includes('product') || aliceText.includes('roadmap');
    const bobHasBackend = bobText.includes('backend') || bobText.includes('infrastructure') || bobText.includes('database');
    const carolHasSprint = carolText.includes('sprint') || carolText.includes('timeline') || carolText.includes('velocity');

    console.log(`   Alice talks about product/roadmap: ${aliceHasProduct ? 'PASS' : 'FAIL'}`);
    console.log(`   Bob talks about backend/infrastructure: ${bobHasBackend ? 'PASS' : 'FAIL'}`);
    console.log(`   Carol talks about sprint/timeline: ${carolHasSprint ? 'PASS' : 'FAIL'}`);

    const allCorrect = aliceHasProduct && bobHasBackend && carolHasSprint;

    // Summary
    console.log('\n=== SUMMARY ===');
    console.log(`   Speakers found: ${speakers.length}`);
    console.log(`   Audio captured: ${audioData.filter(a => !a.error).length}`);
    console.log(`   Transcribed: ${transcriptions.length}`);
    console.log(`   Redis segments: ${segments.length}`);
    console.log(`   Speaker attribution correct: ${allCorrect ? 'YES' : 'NO'}`);
    console.log(`\n   ${allCorrect ? 'PASS: Per-speaker pipeline works end-to-end.' : 'FAIL: Check results above.'}`);

    // Cleanup
    await redis.del(STREAM_KEY);
    await redis.del(SPEAKER_EVENTS_KEY);

  } catch (err) {
    console.error('Error:', err.message);
  } finally {
    await page.close();
    await redis.disconnect();
  }
})().catch(console.error);
