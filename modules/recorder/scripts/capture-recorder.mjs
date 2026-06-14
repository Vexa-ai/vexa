/**
 * capture-recorder — standalone capture.v1 fixture recorder (the collection
 * front door for the extension).
 *
 * A WS server speaking the extension's exact ingest protocol. It writes the ONE
 * faithful format — `stream.capture` (via StreamCaptureWriter) — the SAME bytes
 * Lane-2's prod-dump tees produce and the SAME format the replay tools read
 * (mixed-replay, fixture-feed, bench:view). So a fixture collected here from a
 * live meeting round-trips identically to one dumped from production: that
 * shared contract is what makes "collect once, replay forever" work.
 *
 *   node modules/recorder/scripts/capture-recorder.mjs          # port 9099
 *   FIXTURE_NAME=teams-mixed-2026-06-14 node …                  # name the output
 *
 * Point the extension's ingest URL at  ws://localhost:9099/ingest, join, Start.
 * On stop the fixture dir prints; replay it with:
 *   cd ../pipeline && npm run replay:mixed -- <dir>     (or bench:view)
 *
 * Wire protocol (from ingest-server.ts):
 *   connect  ws://host:PORT/ingest?platform=<p>&native_meeting_id=<id>&api_key=<k>
 *   server→  {type:'ready', meeting_id}
 *   client→  binary capture.v1 audio frame   ·   client→ text capture.v1 event (JSON)
 */
import { WebSocketServer } from 'ws';
import { appendFileSync, writeFileSync } from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { StreamCaptureWriter, decodeAudioFrame, decodeEvent } from '../dist/index.js';

const PORT = parseInt(process.env.PORT || '9099', 10);
const DIAG_FILE = process.env.DIAG_FILE || '/tmp/vexa-diag.jsonl';
const FIXTURE_ROOT = process.env.VEXA_FIXTURE_CACHE || path.join(os.homedir(), '.vexa', 'fixtures');
try { writeFileSync(DIAG_FILE, ''); } catch { /* ignore */ }

// Never die on a bad frame or a socket reset — fixture capture must survive
// whatever the extension throws at it.
process.on('uncaughtException', (e) => console.error('[capture-recorder] uncaught:', e?.message || e));
process.on('unhandledRejection', (e) => console.error('[capture-recorder] unhandledRejection:', e?.message || e));

const wss = new WebSocketServer({ port: PORT });
wss.on('error', (e) => console.error('[capture-recorder] server error:', e?.message || e));
console.log(`[capture-recorder] listening ws://localhost:${PORT}/ingest`);
console.log(`[capture-recorder] point the extension's ingest URL here, then join + Start.`);

wss.on('connection', (ws, req) => {
  ws.on('error', (e) => console.error('[capture-recorder] ws error:', e?.message || e));
  const url = new URL(req.url || '', `http://localhost:${PORT}`);
  const platform = url.searchParams.get('platform') || 'unknown';
  const nativeMeetingId = url.searchParams.get('native_meeting_id') || `local-${process.hrtime.bigint()}`;
  const language = url.searchParams.get('language');
  const fixtureName = process.env.FIXTURE_NAME || `${platform}-${nativeMeetingId}`;
  const outDir = path.join(FIXTURE_ROOT, 'capture', 'v1', fixtureName);

  // The ONE faithful writer — verbatim wire bytes, identical to Lane-2's tees.
  const writer = new StreamCaptureWriter(outDir, {
    platform, nativeMeetingId,
    language: language && language !== 'auto' ? language : undefined,
  });
  let chunks = 0, textFrames = 0;
  const seenChannels = new Set();
  console.log(`[capture-recorder] ▶ session platform=${platform} → ${outDir}`);

  ws.send(JSON.stringify({ type: 'ready', meeting_id: fixtureName }));

  ws.on('message', (data, isBinary) => {
    try {
      const b = Buffer.from(data);
      if (isBinary) {
        writer.rawAudio(b);                 // faithful tee — verbatim wire bytes
        const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength); // decode for logging only
        if (f && !seenChannels.has(f.speakerIndex)) {
          seenChannels.add(f.speakerIndex);
          const label = f.speakerIndex === 1000 ? 'MIC (You)' : f.speakerIndex === 999 ? 'MIXED REMOTE' : `track ${f.speakerIndex}`;
          console.log(`[capture-recorder] ▶▶ NEW CHANNEL ${f.speakerIndex} = ${label}  (channels: ${[...seenChannels].join(',')})`);
        }
        if (++chunks % 100 === 0) console.log(`[capture-recorder] audio=${chunks} events=${textFrames} channels=[${[...seenChannels].join(',')}]`);
        return;
      }
      const raw = b.toString('utf8');
      // Rich telemetry frame (NOT a capture.v1 event) — dump to DIAG_FILE, don't record.
      if (raw.startsWith('{"type":"diag"')) {
        appendFileSync(DIAG_FILE, raw + '\n');
        return;
      }
      writer.rawEvent(b);                   // faithful tee — verbatim event bytes (incl. chat)
      textFrames++;
      const ev = decodeEvent(raw);          // decode for logging only
      console.log(`[capture-recorder] event #${textFrames} ${ev ? ev.kind + (ev.speaker ? ' speaker=' + ev.speaker : '') : 'REJECTED: ' + raw.slice(0, 100)}`);
    } catch (e) { console.error('[capture-recorder] message error (continuing):', e?.message || e); }
  });

  const finish = async () => {
    try {
      const dir = await writer.finalize();
      console.log(`\n[capture-recorder] ■ fixture: ${dir}  (audio=${chunks} events=${textFrames} channels=[${[...seenChannels].join(',')}])`);
      console.log(`[capture-recorder]   replay: cd ../pipeline && npm run replay:mixed -- ${dir}`);
      console.log(`[capture-recorder]   or view: cd ../pipeline && npm run bench:view -- ${dir}`);
      if (process.env.FIXTURE_S3 === '1') console.log(`[capture-recorder]   (S3 push of stream.capture: TODO — fixture is local for now)`);
    } catch (e) {
      console.error(`[capture-recorder] finalize error:`, e?.message || e);
    }
  };
  ws.on('close', finish);
  ws.on('error', finish);
});
