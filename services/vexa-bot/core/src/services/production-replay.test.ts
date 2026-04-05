/**
 * Production-faithful replay test.
 *
 * TWO MODES:
 *
 *   CORE (default):
 *     Replays audio through SpeakerStreamManager + Whisper, scores against
 *     ground truth. Fast, isolated, no infra dependencies beyond Whisper.
 *     Use: make play-replay DATASET=teams-3sp-collection
 *
 *   FULL SYSTEM (PUBLISH=true):
 *     Same pipeline, but also publishes segments to Redis via SegmentPublisher.
 *     Creates a meeting record so transcription-collector processes segments
 *     into Redis Hash → WS → Postgres. View live in observe.html.
 *     Use: make play-replay-full DATASET=teams-3sp-collection
 *
 * Reproduces the EXACT index.ts code path:
 *   audio -> SpeakerStreamManager -> onSegmentReady (real Whisper + word storage)
 *   -> handleTranscriptionResult -> confirm/idle -> onSegmentConfirmed
 *   -> SegmentPublisher (PUBLISH mode) or in-memory array (core mode)
 *
 * Uses real collected data:
 *   - Per-utterance TTS audio files placed at caption speaker-change times
 *   - Real caption events replayed at recorded timestamps
 *   - Real Whisper (word timestamps)
 *   - flushSpeaker on caption speaker changes (same as handleTeamsCaptionData)
 *
 * Run: npx ts-node core/src/services/production-replay.test.ts <audio-dir> <tests-dir>
 *
 * Env vars:
 *   DATASET              - dataset directory name (default: collection-run)
 *   PUBLISH              - if "true", publish to Redis/WS/DB (full system mode)
 *   REDIS_URL            - Redis URL for full system mode (default: redis://localhost:6379)
 *   API_GATEWAY_URL      - API gateway for meeting creation (default: http://localhost:8066)
 *   API_TOKEN            - API token for meeting creation
 *   ADMIN_TOKEN          - JWT signing secret (default: changeme)
 *   NATIVE_MEETING_ID    - fake meeting ID for full system mode (default: auto-generated)
 *   MEETING_ID           - pre-created meeting ID (skips meeting creation, for delivery test)
 */

import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { SpeakerStreamManager } from './speaker-streams';
import { TranscriptionClient } from './transcription-client';
import { SegmentPublisher } from './segment-publisher';
import { mapWordsToSpeakers, captionsToSpeakerBoundaries, CaptionEvent, TimestampedWord } from './speaker-mapper';

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096;
const CHUNK_DURATION_MS = (CHUNK_SIZE / SAMPLE_RATE) * 1000;
const TX_URL = process.env.TRANSCRIPTION_URL || 'http://localhost:8085/v1/audio/transcriptions';
const TX_TOKEN = process.env.TRANSCRIPTION_TOKEN || '32c59b9f654f1b6e376c6f020d79897d';
const PUBLISH = process.env.PUBLISH === 'true';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const API_GATEWAY_URL = process.env.API_GATEWAY_URL || 'http://localhost:8066';
const API_TOKEN = process.env.API_TOKEN || '';
const ADMIN_TOKEN_SECRET = process.env.ADMIN_TOKEN || 'changeme';

const AUDIO_DIR = process.argv[2] || `${__dirname}/../../../../features/realtime-transcription/data/raw`;
const TESTS_DIR = process.argv[3] || `${__dirname}/../../../../features/realtime-transcription/tests`;

// -- JWT helper for minting MeetingTokens (full system mode) ------------------

function mintMeetingToken(meetingId: number, userId: number, platform: string, nativeMeetingId: string): string {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const payload = Buffer.from(JSON.stringify({
    meeting_id: meetingId, user_id: userId, platform, native_meeting_id: nativeMeetingId,
    scope: 'transcribe:write', iss: 'meeting-api', aud: 'transcription-collector',
    iat: now, exp: now + 7200, jti: crypto.randomUUID(),
  })).toString('base64url');
  const sig = crypto.createHmac('sha256', ADMIN_TOKEN_SECRET)
    .update(`${header}.${payload}`).digest('base64url');
  return `${header}.${payload}.${sig}`;
}

// -- Create meeting via API (full system mode) --------------------------------

async function createReplayMeeting(dataset: string): Promise<{ meetingId: number; nativeMeetingId: string }> {
  // Must be a valid 13-digit numeric Teams native ID for WS authorize-subscribe validation
  const nativeMeetingId = process.env.NATIVE_MEETING_ID || `${Date.now()}`.slice(0, 13);

  // Create meeting directly in DB (bypasses meeting-api which tries to launch a real bot)
  const http = await import('http');
  const adminResp = await new Promise<any>((resolve, reject) => {
    const data = JSON.stringify({ email: 'test@vexa.ai', name: 'Test User' });
    // Get user ID first
    const req = http.request({
      hostname: 'localhost', port: 8067,
      path: '/admin/users/email/test@vexa.ai',
      method: 'GET',
      headers: { 'X-Admin-API-Key': ADMIN_TOKEN_SECRET },
    }, (res: any) => {
      let b = ''; res.on('data', (c: string) => b += c);
      res.on('end', () => { try { resolve(JSON.parse(b)); } catch { resolve({}); } });
    });
    req.on('error', reject);
    req.end();
  });
  const userId = adminResp?.id || 1;

  // Insert meeting directly via admin SQL
  const insertResult = await new Promise<string>((resolve, reject) => {
    const proc = require('child_process');
    proc.exec(
      `docker exec vexa-restore-postgres-1 psql -U postgres -d vexa_restore -c "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data, created_at, updated_at) VALUES (${userId}, 'teams', '${nativeMeetingId}', 'active', '{}'::jsonb, now(), now()) RETURNING id;"`,
      (err: any, stdout: string) => {
        if (err) reject(err);
        else resolve(stdout);
      }
    );
  });
  const meetingIdMatch = insertResult.match(/(\d+)/);
  if (!meetingIdMatch) throw new Error(`Failed to create replay meeting: ${insertResult}`);
  const meetingId = parseInt(meetingIdMatch[1]);

  return { meetingId, nativeMeetingId };
}

// -- WAV reader ---------------------------------------------------------------

function readWavAsFloat32(wavPath: string): Float32Array {
  const buf = fs.readFileSync(wavPath);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error(`Not a WAV: ${wavPath}`);
  const sampleRate = buf.readUInt32LE(24);
  const bitsPerSample = buf.readUInt16LE(34);
  let dataOffset = 36;
  while (dataOffset < buf.length - 8) {
    if (buf.toString('ascii', dataOffset, dataOffset + 4) === 'data') { dataOffset += 8; break; }
    dataOffset += 8 + buf.readUInt32LE(dataOffset + 4);
  }
  const totalSamples = (buf.length - dataOffset) / (bitsPerSample / 8);
  const original = new Float32Array(totalSamples);
  for (let i = 0; i < totalSamples; i++) {
    original[i] = bitsPerSample === 16 ? buf.readInt16LE(dataOffset + i * 2) / 32768 : buf.readFloatLE(dataOffset + i * 4);
  }
  if (sampleRate === SAMPLE_RATE) return original;
  const ratio = SAMPLE_RATE / sampleRate;
  const resampled = new Float32Array(Math.floor(totalSamples * ratio));
  for (let i = 0; i < resampled.length; i++) {
    const srcIdx = i / ratio;
    const lo = Math.floor(srcIdx);
    const hi = Math.min(lo + 1, totalSamples - 1);
    resampled[i] = original[lo] * (1 - srcIdx + lo) + original[hi] * (srcIdx - lo);
  }
  return resampled;
}

// -- Parse real caption events from collected data ----------------------------

interface RealCaptionEvent {
  type: 'caption' | 'speaker_change';
  speaker: string;
  text?: string;
  from?: string;
  to?: string;
  relTimeSec: number;
}

function parseRealEvents(eventsPath: string): RealCaptionEvent[] {
  const lines = fs.readFileSync(eventsPath, 'utf8').split('\n');
  const events: RealCaptionEvent[] = [];
  let firstTs: number | null = null;

  for (const line of lines) {
    const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2}T[\d:.]+)Z/);
    if (!tsMatch) continue;
    const tsStr = tsMatch[1].replace(/(\.\d{6})\d+/, '$1');
    const ts = new Date(tsStr).getTime() / 1000;
    if (firstTs === null) firstTs = ts;
    const rel = ts - firstTs;

    const capMatch = line.match(/TEAMS CAPTION.*"([^"]+)": (.+)/);
    if (capMatch) {
      events.push({ type: 'caption', speaker: capMatch[1], text: capMatch[2], relTimeSec: rel });
      continue;
    }

    const changeMatch = line.match(/Speaker change: (.+?) \u2192 (.+?) \(Guest\)/);
    if (changeMatch) {
      events.push({ type: 'speaker_change', speaker: changeMatch[2].trim(), from: changeMatch[1].trim(), to: changeMatch[2].trim(), relTimeSec: rel });
    }
  }
  return events;
}

// -- Parse speaker change times for audio placement ---------------------------

function parseSpeakerChangeTimes(eventsPath: string): { speaker: string; relTimeSec: number }[] {
  const lines = fs.readFileSync(eventsPath, 'utf8').split('\n');
  const changes: { speaker: string; relTimeSec: number }[] = [];
  let firstTs: number | null = null;

  for (const line of lines) {
    const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2}T[\d:.]+)Z/);
    if (!tsMatch) continue;
    const tsStr = tsMatch[1].replace(/(\.\d{6})\d+/, '$1');
    const ts = new Date(tsStr).getTime() / 1000;
    if (firstTs === null) firstTs = ts;

    const changeMatch = line.match(/Speaker change: (.+?) \u2192 (.+?) \(Guest\)/);
    if (changeMatch) {
      changes.push({ speaker: changeMatch[2].trim(), relTimeSec: ts - firstTs });
    }
  }
  return changes;
}

// -- Ground truth from .txt files ---------------------------------------------

interface GTUtterance {
  speaker: string;
  text: string;
  offsetSec: number;
  audioFile: string;
}

function loadGroundTruth(collectionDir: string, speakerChangeTimes: { speaker: string; relTimeSec: number }[]): GTUtterance[] {
  // Audio files in order: 01-alice-roadmap.wav ... 17-charlie-greatmeeting.wav
  // Check audio/ subdirectory first, then root
  const audioDir = fs.existsSync(path.join(collectionDir, 'audio')) ? path.join(collectionDir, 'audio') : collectionDir;
  const wavFiles = fs.readdirSync(audioDir)
    .filter(f => f.endsWith('.wav'))
    .sort();

  const gt: GTUtterance[] = [];

  // Fallback times if speaker changes don't match
  const fallbackTimes = [0, 25, 35, 43, 47, 51, 67, 72, 78, 84, 87, 90, 93, 99, 114, 117, 120];

  for (let i = 0; i < wavFiles.length; i++) {
    const wavFile = wavFiles[i];
    const txtFile = wavFile.replace('.wav', '.txt');
    const txtPath = path.join(audioDir, txtFile);
    if (!fs.existsSync(txtPath)) continue;

    const text = fs.readFileSync(txtPath, 'utf8').trim();

    // Extract speaker from filename: NN-firstname-lastname.wav or NN-firstname-lastname-org.wav
    // Match against speaker change events to get the canonical caption name
    const baseName = wavFile.replace(/^\d+-/, '').replace(/\.wav$/, '');
    const baseWords = baseName.split('-');
    let speaker = 'Unknown';
    if (i < speakerChangeTimes.length) {
      // Use the canonical speaker name from caption events
      speaker = speakerChangeTimes[i].speaker;
    } else {
      // Fallback: title-case the first word
      speaker = baseWords[0].charAt(0).toUpperCase() + baseWords[0].slice(1);
    }

    // Use parsed speaker change time or fallback
    const offsetSec = i < speakerChangeTimes.length ? speakerChangeTimes[i].relTimeSec : (i < fallbackTimes.length ? fallbackTimes[i] : i * 5);

    gt.push({ speaker, text, offsetSec, audioFile: wavFile });
  }

  return gt;
}

// -- Main ---------------------------------------------------------------------

async function main() {
  const dataset = process.env.DATASET || 'teams-3sp-collection';
  const mode = PUBLISH ? 'FULL SYSTEM' : 'CORE';
  console.log('\n  ====================================================');
  console.log(`  PRODUCTION-FAITHFUL REPLAY TEST (${dataset})`);
  console.log(`  Mode: ${mode}${PUBLISH ? ' → Redis → WS → DB' : ' (pipeline only)'}`);
  console.log('  ====================================================\n');

  const collectionDir = path.join(AUDIO_DIR, dataset);
  // Events file: check inside dataset dir first, then tests dir
  const eventsInDataset = path.join(collectionDir, 'events.txt');
  const eventsInTests = path.join(TESTS_DIR, `${dataset}-events.txt`);
  const eventsPath = fs.existsSync(eventsInDataset) ? eventsInDataset : eventsInTests;

  // -- 1. Parse speaker change times and load ground truth -------------------

  const speakerChangeTimes = parseSpeakerChangeTimes(eventsPath);
  console.log(`  Speaker changes parsed: ${speakerChangeTimes.length}`);
  for (const sc of speakerChangeTimes) {
    console.log(`    ${sc.relTimeSec.toFixed(1)}s -> ${sc.speaker}`);
  }

  const GT = loadGroundTruth(collectionDir, speakerChangeTimes);
  console.log(`\n  Ground truth utterances: ${GT.length}`);
  for (const g of GT) {
    console.log(`    ${g.offsetSec.toFixed(1)}s ${g.speaker}: "${g.text.substring(0, 50)}..." [${g.audioFile}]`);
  }

  // -- 2. Load real caption events -------------------------------------------

  const realEvents = parseRealEvents(eventsPath);
  const captionEvents = realEvents.filter(e => e.type === 'caption');
  const speakerChangeEvents = realEvents.filter(e => e.type === 'speaker_change');

  console.log(`\n  Real data: ${captionEvents.length} captions, ${speakerChangeEvents.length} speaker changes`);

  // -- 3. Load per-utterance audio (no mixing — each speaker gets own channel) -

  const audioEntries: { audio: Float32Array; offsetSec: number; speaker: string; endSec: number }[] = [];
  let maxEnd = 0;

  for (const g of GT) {
    const audioSubDir = fs.existsSync(path.join(collectionDir, 'audio')) ? path.join(collectionDir, 'audio') : collectionDir;
    const wavPath = path.join(audioSubDir, g.audioFile);
    try {
      const audio = readWavAsFloat32(wavPath);
      const endSec = g.offsetSec + audio.length / SAMPLE_RATE;
      if (endSec > maxEnd) maxEnd = endSec;
      audioEntries.push({ audio, offsetSec: g.offsetSec, speaker: g.speaker, endSec });
    } catch (e: any) {
      console.log(`  WARNING: Could not load ${g.audioFile}: ${e.message}`);
    }
  }

  const totalDurSec = Math.ceil(maxEnd) + 10; // pad 10s
  console.log(`  Per-speaker audio: ${totalDurSec}s total, ${audioEntries.length} utterances loaded\n`);

  // -- 4. Set up pipeline -- EXACT index.ts wiring ---------------------------

  const txClient = new TranscriptionClient({
    serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE,
  });

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE, minAudioDuration: 3, submitInterval: 3,
    confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15,
  });

  const t0 = Date.now();
  const ts = () => ((Date.now() - t0) / 1000).toFixed(1);
  const sessionStartMs = t0;

  // -- Full system mode: create meeting + publisher --
  let publisher: SegmentPublisher | null = null;
  let replayMeetingId: number | null = null;
  let replayNativeId: string | null = null;

  if (PUBLISH) {
    if (!API_TOKEN) {
      console.error('  PUBLISH=true requires API_TOKEN env var');
      process.exit(1);
    }

    // Support pre-created meeting (for delivery test orchestration)
    const preCreatedMeetingId = process.env.MEETING_ID ? parseInt(process.env.MEETING_ID) : null;
    const preCreatedNativeId = preCreatedMeetingId ? (process.env.NATIVE_MEETING_ID || null) : null;

    let meeting: { meetingId: number; nativeMeetingId: string };
    if (preCreatedMeetingId && preCreatedNativeId) {
      console.log(`  [PUBLISH] Using pre-created meeting ${preCreatedMeetingId} (native: ${preCreatedNativeId})`);
      meeting = { meetingId: preCreatedMeetingId, nativeMeetingId: preCreatedNativeId };
    } else {
      console.log('  [PUBLISH] Creating replay meeting...');
      meeting = await createReplayMeeting(dataset);
    }

    replayMeetingId = meeting.meetingId;
    replayNativeId = meeting.nativeMeetingId;
    const token = mintMeetingToken(meeting.meetingId, 1, 'teams', meeting.nativeMeetingId);
    const sessionUid = `replay-${dataset}-${Date.now()}`;

    publisher = new SegmentPublisher({
      redisUrl: REDIS_URL,
      meetingId: String(meeting.meetingId),
      token,
      sessionUid,
      platform: 'teams',
    });
    publisher.resetSessionStart();
    await publisher.publishSessionStart();
    console.log(`  [PUBLISH] Meeting ${meeting.meetingId} (native: ${meeting.nativeMeetingId})`);
    console.log(`  [PUBLISH] Observe: http://localhost:3012/observe.html?meeting=${meeting.nativeMeetingId}&auto=1`);
    console.log(`  [PUBLISH] Dashboard: http://localhost:3011/meetings/${meeting.meetingId}`);
    console.log('');
  }

  // === index.ts globals ===
  // Per-speaker word storage: words are set in onSegmentReady and consumed in
  // onSegmentConfirmed. A shared global would race across concurrent speakers
  // (confirmation fires on the Nth match, by which time another speaker's words
  // may have overwritten the global). Keyed by speakerId.
  const whisperWordsPerSpeaker: Map<string, TimestampedWord[]> = new Map();
  const captionEventLog: CaptionEvent[] = [];
  let lastCaptionSpeakerId: string | null = null;
  const outputSegments: { speaker: string; text: string; start: number; end: number }[] = [];
  // Core stream (legacy): every segment emission (draft + confirmed), time-ordered
  const coreStream: { ts: string; segment_id: string; speaker: string; text: string; start: number; end: number; completed: boolean; absolute_start_time: string; absolute_end_time: string }[] = [];
  // Core transcript: new format — per-tick (confirmed[], pending[]) bundles per speaker
  const coreTranscript: { ts: string; speaker: string; confirmed: any[]; pending: any[] }[] = [];
  // Per-speaker batch: onSegmentConfirmed adds to the speaker's batch
  const confirmedBatches = new Map<string, import('./segment-publisher').TranscriptionSegment[]>();

  // === Telemetry ===
  const telemetry = {
    whisperCalls: 0,
    whisperFailures: 0,
    totalWhisperMs: 0,
    draftsEmitted: 0,
    segmentsConfirmed: 0,
    segmentsDiscarded: 0,
    totalConfirmLatencyMs: 0,
    whisperSegmentCounts: [] as number[],
    segmentDurations: [] as number[],
    segmentWordCounts: [] as number[],
  };

  // === onSegmentReady -- same as index.ts ===
  mgr.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    const callStart = Date.now();
    telemetry.whisperCalls++;
    try {
      const result = await txClient.transcribe(audioBuffer);
      telemetry.totalWhisperMs += Date.now() - callStart;
      if (result?.text) {
        telemetry.whisperSegmentCounts.push(result.segments?.length || 0);
        const text = result.text.trim();
        const words = result.segments?.flatMap(s => s.words || []) || [];
        if (words.length > 0) {
          whisperWordsPerSpeaker.set(speakerId, words);
        }

        // Emit draft — same segment_id as confirmed, HSET overwrites
        {
          const bufStart = mgr.getBufferStartMs(speakerId);
          const nowMs = Date.now();
          const startSec = (bufStart - sessionStartMs) / 1000;
          const endSec = (nowMs - sessionStartMs) / 1000;
          const segId = mgr.getSegmentId(speakerId);
          const absStart = new Date(bufStart).toISOString();
          const absEnd = new Date(nowMs).toISOString();

          coreStream.push({ ts: new Date().toISOString(), segment_id: segId, speaker: speakerName, text, start: startSec, end: endSec, completed: false, absolute_start_time: absStart, absolute_end_time: absEnd });

          // Build pending segment (current unconfirmed text)
          const pendingSeg: import('./segment-publisher').TranscriptionSegment = {
            speaker: speakerName, text, start: startSec, end: endSec,
            language: result.language || 'en', completed: false,
            absolute_start_time: absStart, absolute_end_time: absEnd,
          };

          // Drain only this speaker's confirmed batch
          const newConfirmed = confirmedBatches.get(speakerId) || [];
          confirmedBatches.set(speakerId, []);

          // Build per-segment pending from Whisper segments
          const whisperPendingSegs: import('./segment-publisher').TranscriptionSegment[] =
            (result.segments || [{ text, start: 0, end: 0 }]).map((ws: any) => ({
              speaker: speakerName,
              text: (ws.text || '').trim(),
              start: startSec + (ws.start || 0),
              end: startSec + (ws.end || 0),
              language: result.language || 'en',
              completed: false,
              absolute_start_time: new Date(bufStart + (ws.start || 0) * 1000).toISOString(),
              absolute_end_time: new Date(bufStart + (ws.end || 0) * 1000).toISOString(),
            })).filter((s: any) => s.text);

          const mapSeg = (s: import('./segment-publisher').TranscriptionSegment) => ({
            segment_id: s.segment_id, speaker: s.speaker, text: s.text,
            start: s.start, end: s.end, completed: s.completed,
            absolute_start_time: s.absolute_start_time, absolute_end_time: s.absolute_end_time,
          });

          // Filter out pending segments that overlap with just-confirmed text
          const confirmedTextList = newConfirmed.map(c => c.text.trim());
          const pending = whisperPendingSegs.filter(p => {
            const pt = (p.text || '').trim();
            return !confirmedTextList.some(ct => pt === ct || pt.startsWith(ct) || ct.startsWith(pt));
          });

          coreTranscript.push({
            ts: new Date().toISOString(), speaker: speakerName,
            confirmed: newConfirmed.map(mapSeg), pending: pending.map(mapSeg),
          });

          if (publisher) {
            telemetry.draftsEmitted++;
            await publisher.publishTranscript(speakerName, newConfirmed, pending);
          }
        }

        const lastSeg = result.segments?.[result.segments.length - 1];
        const whisperSegs = result.segments?.map(s => ({
          text: s.text, start: s.start, end: s.end
        }));
        mgr.handleTranscriptionResult(speakerId, text, lastSeg?.end, whisperSegs);
      } else {
        mgr.handleTranscriptionResult(speakerId, '');
      }
    } catch (err: any) {
      telemetry.whisperFailures++;
      telemetry.totalWhisperMs += Date.now() - callStart;
      mgr.handleTranscriptionResult(speakerId, '');
    }
  };

  // === onSegmentConfirmed -- same as index.ts with mapper ===
  mgr.onSegmentConfirmed = (speakerId, speakerName, transcript, bufferStartMs, bufferEndMs, segmentId) => {
    const startSec = (bufferStartMs - sessionStartMs) / 1000;
    const endSec = (bufferEndMs - sessionStartMs) / 1000;
    const durSec = endSec - startSec;
    const wordCount = transcript.split(/\s+/).length;
    const confirmLatencyMs = bufferEndMs - bufferStartMs;

    telemetry.segmentsConfirmed++;
    telemetry.totalConfirmLatencyMs += confirmLatencyMs;
    telemetry.segmentDurations.push(durSec);
    telemetry.segmentWordCounts.push(wordCount);

    const absStart = new Date(bufferStartMs).toISOString();
    const absEnd = new Date(bufferEndMs).toISOString();
    coreStream.push({ ts: new Date().toISOString(), segment_id: segmentId, speaker: speakerName, text: transcript, start: startSec, end: endSec, completed: true, absolute_start_time: absStart, absolute_end_time: absEnd });

    const speakerWords = whisperWordsPerSpeaker.get(speakerId) || [];
    console.log(`  [${ts()}s] CONFIRMED | ${speakerName} | ${durSec.toFixed(1)}s ${wordCount}w latency=${(confirmLatencyMs/1000).toFixed(1)}s | "${transcript.substring(0, 60)}..."`);

    // Collect into this speaker's batch — published with their next tick
    const fullSegmentId = publisher ? `${publisher.sessionUid}:${segmentId}` : segmentId;
    if (!confirmedBatches.has(speakerId)) confirmedBatches.set(speakerId, []);
    confirmedBatches.get(speakerId)!.push({
      speaker: speakerName, text: transcript, start: startSec, end: endSec,
      language: 'en', completed: true, segment_id: fullSegmentId,
      absolute_start_time: absStart, absolute_end_time: absEnd,
    });

    // Per-speaker audio already provides correct attribution.
    // The mapper was disabled in the live pipeline (index.ts) because
    // carry-forward was removed — re-attributing words based on caption
    // boundary timing only introduces split errors.
    outputSegments.push({ speaker: speakerName, text: transcript, start: startSec, end: endSec });
  };

  // -- 5. Schedule caption events from real data ------------------------------

  // Rebase caption times: first caption at 0s = audio at 0s
  const firstCaptionTime = captionEvents.length > 0 ? captionEvents[0].relTimeSec : 0;

  for (const ce of captionEvents) {
    const rebasedTime = ce.relTimeSec - firstCaptionTime;
    const delayMs = Math.max(0, rebasedTime * 1000);

    setTimeout(() => {
      const speakerId = `teams-${ce.speaker.replace(/\s+/g, '_')}`;

      // Accumulate for mapper boundaries
      captionEventLog.push({ speaker: ce.speaker, text: ce.text || '', timestamp: rebasedTime });

      // Speaker change -> flush previous (same as handleTeamsCaptionData)
      if (lastCaptionSpeakerId && lastCaptionSpeakerId !== speakerId) {
        mgr.flushSpeaker(lastCaptionSpeakerId);
      }
      lastCaptionSpeakerId = speakerId;

      // Add speaker if new
      if (!mgr.hasSpeaker(speakerId)) {
        mgr.addSpeaker(speakerId, ce.speaker);
      }
    }, delayMs);
  }

  // -- 6. Feed per-speaker audio at real-time speed ----------------------------
  //
  // In the real pipeline, each speaker has their OWN audio channel (per-speaker
  // audio from the browser). Here we replicate that: each utterance's audio
  // is fed ONLY to that speaker's channel. No mixing.

  console.log(`  [0.0s] Playing ${totalDurSec}s of audio...\n`);

  const totalSamples = totalDurSec * SAMPLE_RATE;
  const totalChunks = Math.ceil(totalSamples / CHUNK_SIZE);

  for (let i = 0; i < totalChunks; i++) {
    const chunkStartSample = i * CHUNK_SIZE;
    const chunkEndSample = Math.min(chunkStartSample + CHUNK_SIZE, totalSamples);
    const audioSec = chunkStartSample / SAMPLE_RATE;

    // For each utterance that overlaps this chunk, extract and feed its audio
    for (const ae of audioEntries) {
      const uttStartSample = Math.floor(ae.offsetSec * SAMPLE_RATE);
      const uttEndSample = uttStartSample + ae.audio.length;

      // Does this utterance overlap with current chunk?
      if (chunkEndSample <= uttStartSample || chunkStartSample >= uttEndSample) continue;

      // Extract the portion of THIS utterance's audio that falls in this chunk
      const srcStart = Math.max(0, chunkStartSample - uttStartSample);
      const srcEnd = Math.min(ae.audio.length, chunkEndSample - uttStartSample);
      const chunk = ae.audio.subarray(srcStart, srcEnd);

      const speakerId = `teams-${ae.speaker.replace(/\s+/g, '_')}_(Guest)`;
      if (!mgr.hasSpeaker(speakerId)) {
        mgr.addSpeaker(speakerId, `${ae.speaker} (Guest)`);
      }
      mgr.feedAudio(speakerId, chunk);
    }

    if (i > 0 && Math.floor(audioSec) % 10 === 0 && Math.floor(((i - 1) * CHUNK_SIZE) / SAMPLE_RATE) % 10 !== 0) {
      console.log(`  [${ts()}s] --- ${audioSec.toFixed(0)}s / ${totalDurSec}s ---`);
    }

    await new Promise(r => setTimeout(r, CHUNK_DURATION_MS));
  }

  console.log(`\n  [${ts()}s] Audio complete. Waiting for final processing...\n`);
  await new Promise(r => setTimeout(r, 5000));

  // Force flush all speakers
  for (const sid of mgr.getActiveSpeakers()) {
    mgr.flushSpeaker(sid, true);
  }
  await new Promise(r => setTimeout(r, 3000));

  // -- 7. Score against ground truth ------------------------------------------

  console.log('  ====================================================');
  console.log('  RESULTS');
  console.log('  ====================================================\n');

  console.log(`  Whisper calls: ${telemetry.whisperCalls} (${telemetry.whisperFailures} failed)`);
  console.log(`  Output segments: ${outputSegments.length}`);
  console.log(`  Caption events accumulated: ${captionEventLog.length}`);
  const totalStoredWords = Array.from(whisperWordsPerSpeaker.values()).reduce((sum, w) => sum + w.length, 0);
  console.log(`  Whisper words stored: ${totalStoredWords} across ${whisperWordsPerSpeaker.size} speakers`);

  // Telemetry summary
  const avgWhisperMs = telemetry.whisperCalls > 0 ? (telemetry.totalWhisperMs / telemetry.whisperCalls).toFixed(0) : 'n/a';
  const avgConfirmLatency = telemetry.segmentsConfirmed > 0 ? (telemetry.totalConfirmLatencyMs / telemetry.segmentsConfirmed / 1000).toFixed(1) : 'n/a';
  const avgWhisperSegs = telemetry.whisperSegmentCounts.length > 0 ? (telemetry.whisperSegmentCounts.reduce((a: number,b: number) => a+b, 0) / telemetry.whisperSegmentCounts.length).toFixed(1) : 'n/a';
  const durs = telemetry.segmentDurations;
  const sortedDurs = [...durs].sort((a,b) => a-b);
  const medianDur = sortedDurs.length > 0 ? sortedDurs[Math.floor(sortedDurs.length/2)].toFixed(1) : 'n/a';
  const maxDur = durs.length > 0 ? Math.max(...durs).toFixed(1) : 'n/a';
  const meanDur = durs.length > 0 ? (durs.reduce((a,b) => a+b, 0) / durs.length).toFixed(1) : 'n/a';

  console.log(`\n  ── Pipeline telemetry ──`);
  console.log(`  Whisper avg latency: ${avgWhisperMs}ms`);
  console.log(`  Whisper segments/call: ${avgWhisperSegs}`);
  console.log(`  Confirm latency (speech→emit): ${avgConfirmLatency}s`);
  console.log(`  Segment duration: median=${medianDur}s mean=${meanDur}s max=${maxDur}s`);
  console.log(`  Segments >15s: ${durs.filter(d => d > 15).length}/${durs.length}`);
  console.log(`  Segments >30s: ${durs.filter(d => d > 30).length}/${durs.length}`);
  console.log('');

  console.log('  Output segments:');
  for (const seg of outputSegments) {
    const spk = seg.speaker.replace(' (Guest)', '');
    console.log(`    [${spk}] ${seg.start.toFixed(1)}s-${seg.end.toFixed(1)}s: "${seg.text.substring(0, 80)}"`);
  }

  // Match GT utterances to output segments by keyword matching
  console.log('\n  Ground truth comparison:');
  let captured = 0;
  let correctSpeaker = 0;

  for (const gt of GT) {
    // Extract keywords: words with length > 3 (avoids "a", "the", "is" false positives).
    // For short utterances (< 4 words), use all words since filtering may remove everything.
    const allWords = gt.text.toLowerCase().replace(/[.,!?'"]/g, '').split(/\s+/);
    const gtWords = allWords.length <= 3 ? allWords : allWords.filter(w => w.length > 3);
    let found = false;
    let speakerCorrect = false;
    let bestMatch = { seg: null as any, matchCount: 0 };

    const isShortGT = allWords.length <= 3;
    for (const seg of outputSegments) {
      const segText = seg.text.toLowerCase().replace(/[.,!?'"]/g, '');
      const rawSegWords = segText.split(/\s+/);
      const segWords = new Set(isShortGT ? rawSegWords : rawSegWords.filter(w => w.length > 3));
      // Exact word matching — no substring, both sides filtered for length
      const matches = gtWords.filter(w => segWords.has(w));
      // Use time proximity as tiebreaker when match counts are equal
      const timeDist = Math.abs(seg.start - gt.offsetSec);
      const prevTimeDist = bestMatch.seg ? Math.abs(bestMatch.seg.start - gt.offsetSec) : Infinity;
      if (matches.length > bestMatch.matchCount ||
          (matches.length === bestMatch.matchCount && matches.length > 0 && timeDist < prevTimeDist)) {
        bestMatch = { seg, matchCount: matches.length };
      }
    }

    // Need at least 1 keyword matched for short utterances, 2 for longer ones
    const threshold = allWords.length <= 2 ? 1 : Math.min(2, gtWords.length);
    if (bestMatch.matchCount >= threshold && bestMatch.seg) {
      found = true;
      const segSpeaker = bestMatch.seg.speaker.replace(' (Guest)', '');
      speakerCorrect = segSpeaker === gt.speaker;
      const mark = speakerCorrect ? 'OK' : `WRONG (got ${segSpeaker})`;
      console.log(`    ${mark} | ${gt.speaker} | "${gt.text.substring(0, 50)}" [${gt.audioFile}]`);
    }

    if (!found) {
      console.log(`    LOST | ${gt.speaker} | "${gt.text.substring(0, 50)}" [${gt.audioFile}]`);
    }

    if (found) captured++;
    if (speakerCorrect) correctSpeaker++;
  }

  console.log(`\n  CAPTURED: ${captured}/${GT.length} (${(captured / GT.length * 100).toFixed(0)}%)`);
  console.log(`  CORRECT SPEAKER: ${correctSpeaker}/${GT.length} (${(correctSpeaker / GT.length * 100).toFixed(0)}%)`);
  console.log(`  MAPPER FIRED: ${outputSegments.some(s => s.speaker !== 'Mixed') ? 'YES' : 'NO'}`);

  // -- Cleanup --
  mgr.removeAll();

  if (publisher) {
    // Flush remaining confirmed batches (last segment per speaker gets stuck)
    for (const [speakerId, batch] of confirmedBatches) {
      if (batch.length > 0) {
        const speakerName = batch[0].speaker;
        console.log(`  [PUBLISH] Flushing ${batch.length} remaining confirmed for ${speakerName}`);
        await publisher.publishTranscript(speakerName, batch, []);
      }
    }
    confirmedBatches.clear();

    console.log(`\n  [PUBLISH] Sending session_end...`);
    await publisher.publishSessionEnd();
    // Wait for transcription-collector to process remaining segments
    console.log(`  [PUBLISH] Waiting 40s for collector to persist (30s immutability + 10s buffer)...`);
    await new Promise(r => setTimeout(r, 40000));
    await publisher.close();
    console.log(`  [PUBLISH] Done. View results:`);
    console.log(`    Dashboard: http://localhost:3011/meetings/${replayMeetingId}`);
    console.log(`    REST: curl -H "X-API-Key: $API_TOKEN" http://localhost:8066/transcripts/teams/${replayNativeId}`);
    console.log(`    Observe: http://localhost:3012/observe.html?meeting=${replayNativeId}`);
  }

  // Save core stream (every draft + confirmed emission, unprocessed)
  const coreDir = path.join(AUDIO_DIR, '..', 'core', dataset);
  fs.mkdirSync(coreDir, { recursive: true });
  fs.writeFileSync(path.join(coreDir, 'stream.json'), JSON.stringify(coreStream, null, 2));
  console.log(`\n  Core stream (legacy): ${coreStream.length} events → ${path.join(coreDir, 'stream.json')}`);

  // Save new format: per-tick (confirmed[], pending[]) bundles — one JSON object per line
  const transcriptPath = path.join(coreDir, 'transcript.jsonl');
  fs.writeFileSync(transcriptPath, coreTranscript.map(t => JSON.stringify(t)).join('\n') + '\n');
  const confirmedCount = coreTranscript.reduce((n, t) => n + t.confirmed.length, 0);
  const pendingCount = coreTranscript.reduce((n, t) => n + t.pending.length, 0);
  console.log(`  Core transcript: ${coreTranscript.length} ticks (${confirmedCount} confirmed, ${pendingCount} pending) → ${transcriptPath}`);

  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
