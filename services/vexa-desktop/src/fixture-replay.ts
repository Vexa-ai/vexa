/**
 * fixture-replay — replay a recorded capture.v1 fixture through the pipeline
 * bricks (SpeakerStreamManager → TranscriptionClient). NO live meeting.
 *   npm run replay -- <fixture-dir>
 * Env: TRANSCRIPTION_SERVICE_URL, TRANSCRIPTION_SERVICE_TOKEN.
 */
import * as fs from 'fs';
import * as path from 'path';
import { SpeakerStreamManager } from '@vexa/gmeet-pipeline';
import { TranscriptionClient } from '@vexa/transcribe-whisper';

const SAMPLE_RATE = 16000;
const fixture = process.argv[2];
const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL!;
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
if (!fixture) { console.error('usage: tsx fixture-replay.ts <fixture-dir>'); process.exit(1); }

function readWavAsFloat32(p: string): Float32Array {
  const buf = fs.readFileSync(p);
  const dataOffset = 44; // standard PCM WAV header
  const samples = (buf.length - dataOffset) / 2;
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i++) out[i] = buf.readInt16LE(dataOffset + i * 2) / 32768;
  return out;
}

(async () => {
  const audioDir = path.join(fixture, 'audio');
  // Pick the largest WAV (early stubs from pre-rename speakers can shadow the real track).
  const wav = fs.readdirSync(audioDir).filter(f => f.endsWith('.wav'))
    .sort((a, b) => fs.statSync(path.join(audioDir, b)).size - fs.statSync(path.join(audioDir, a)).size)[0]!;
  const speakerName = wav.replace(/^\d+-/, '').replace('.wav', '').replace(/-/g, ' ');
  const audio = readWavAsFloat32(path.join(audioDir, wav));
  console.log(`\n  REPLAY: ${wav}  (${(audio.length/SAMPLE_RATE).toFixed(1)}s, speaker="${speakerName}")`);
  console.log('  pipeline: SpeakerStreamManager -> TranscriptionClient (no meeting, no bot)\n');

  const txClient = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 });
  const mgr = new SpeakerStreamManager({ sampleRate: SAMPLE_RATE, minAudioDuration: 3, submitInterval: 3, confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15 });
  const confirmed: string[] = [];

  mgr.onSegmentReady = async (speakerId, _n, audioBuffer) => {
    try {
      const r = await txClient.transcribe(audioBuffer);
      const text = (r?.text || '').trim();
      if (text) console.log(`  WHISPER | ${(audioBuffer.length/SAMPLE_RATE).toFixed(1)}s | "${text}"`);
      mgr.handleTranscriptionResult(speakerId, text, r?.segments?.[r.segments.length-1]?.end);
    } catch (e:any) { console.log('  ERR', e.message); mgr.handleTranscriptionResult(speakerId, ''); }
  };
  mgr.onSegmentConfirmed = (_s, _n, text) => { confirmed.push(text); console.log(`  ✅ CONFIRMED: "${text}"`); };

  mgr.addSpeaker('replay-0', speakerName);
  // feed the recorded audio in 0.1s chunks at real-time
  const chunk = Math.floor(SAMPLE_RATE * 0.1);
  for (let i = 0; i < audio.length; i += chunk) {
    mgr.feedAudio('replay-0', audio.subarray(i, Math.min(i+chunk, audio.length)));
    await new Promise(r => setTimeout(r, 95));
  }
  await new Promise(r => setTimeout(r, 6000)); // drain
  console.log(`\n  === RESULT: ${confirmed.length} confirmed segment(s) ===`);
  console.log('  full transcript:', confirmed.join(' ').trim() || '(none)');
  process.exit(0);
})();
