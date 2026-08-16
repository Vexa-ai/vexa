/**
 * L3 — transcript-redis adapter (redis stream + pub/sub egress). OFFLINE, NO real redis.
 *
 * Injects a fake client recording every xAdd/publish and asserts:
 *   • XADD hits the `transcription_segments` stream with id '*' and ONE `payload` field whose
 *     JSON is `{ type: 'transcription', ...segment }`;
 *   • that payload round-trips a transcript.v1-VALID TranscriptSegment (ajv against the published
 *     transcript.schema.json — same pattern as orchestrator.test.ts);
 *   • PUBLISH hits `tc:meeting:{meetingId}:mutable` with `{ type: 'transcript', meeting:{id}, segment }`.
 * Run: npx tsx src/adapters/transcript-redis.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRedisTranscriptSink, TRANSCRIPTION_STREAM, mutableChannel, type RedisTranscriptClient } from './transcript-redis.js';
import type { TranscriptSegment } from '../contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// ── transcript.v1 validator (ajv against the PUBLISHED schema, loaded by path; P8) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const TX_SCHEMA = join(HERE, '..', '..', '..', '..', 'contracts', 'transcript.v1', 'transcript.schema.json');
const txSchema = JSON.parse(readFileSync(TX_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(txSchema);
const validateSeg: ValidateFunction = ajv.compile({ $ref: `${txSchema.$id}#/$defs/TranscriptSegment` });

interface XAddCall { key: string; id: string; fields: Record<string, string> }
interface PubCall { channel: string; message: string }
function fakeClient() {
  const xadds: XAddCall[] = [];
  const pubs: PubCall[] = [];
  const client: RedisTranscriptClient = {
    async xAdd(key, id, fields) { xadds.push({ key, id, fields }); return '1-0'; },
    async publish(channel, message) { pubs.push({ channel, message }); return 1; },
  };
  return { client, xadds, pubs };
}

async function main(): Promise<void> {
  const seg: TranscriptSegment = {
    segment_id: 'sess-uid:s1:0', speaker: 'Alice', speaker_key: 's1', text: 'hello world',
    start: 0, end: 1.2, language: 'en', completed: true, source: 'glow-bound', confidence: 0.97,
    words: [{ word: 'hello', start: 0, end: 0.5, probability: 0.99 }, { word: 'world', start: 0.6, end: 1.2 }],
  };

  // ── publish one segment → assert both legs ──
  {
    const { client, xadds, pubs } = fakeClient();
    const sink = createRedisTranscriptSink({ client, meetingId: 42 });
    await sink.publish(seg);

    // Leg 1 — durable stream
    check('xAdd: exactly one', xadds.length === 1, String(xadds.length));
    check('xAdd: key = transcription_segments', xadds[0]?.key === TRANSCRIPTION_STREAM, xadds[0]?.key);
    check('xAdd: id = *', xadds[0]?.id === '*', xadds[0]?.id);
    check('xAdd: single `payload` field', JSON.stringify(Object.keys(xadds[0]?.fields ?? {})) === JSON.stringify(['payload']), JSON.stringify(Object.keys(xadds[0]?.fields ?? {})));

    const payload = JSON.parse(xadds[0]!.fields.payload) as Record<string, unknown>;
    check('xAdd: payload type = transcription', payload.type === 'transcription', String(payload.type));
    // The collector ingest envelope: { type, meeting_id, segments:[…] } — meeting_id routes, the list drains.
    check('xAdd: payload carries meeting_id', payload.meeting_id !== undefined && payload.meeting_id !== null, String(payload.meeting_id));
    const segs = payload.segments as Array<Record<string, unknown>>;
    check('xAdd: payload.segments is a one-element list with the segment',
      Array.isArray(segs) && segs.length === 1 && segs[0]?.segment_id === seg.segment_id && segs[0]?.text === 'hello world',
      JSON.stringify(payload.segments));

    // segments[0] round-trips a transcript.v1-VALID segment (P8)
    check('xAdd: payload.segments[0] round-trips a transcript.v1-valid segment', !!validateSeg(segs?.[0]), ajv.errorsText(validateSeg.errors));

    // Leg 2 — live mutable channel
    check('publish: exactly one', pubs.length === 1, String(pubs.length));
    check('publish: channel = tc:meeting:42:mutable', pubs[0]?.channel === mutableChannel(42), pubs[0]?.channel);
    check('publish: channel matches the documented format', pubs[0]?.channel === 'tc:meeting:42:mutable', pubs[0]?.channel);
    const msg = JSON.parse(pubs[0]!.message) as { type: string; meeting: { id: unknown }; segment: TranscriptSegment };
    check('publish: type = transcript', msg.type === 'transcript', msg.type);
    check('publish: meeting.id threaded', msg.meeting.id === 42, String(msg.meeting.id));
    check('publish: segment carried verbatim', JSON.stringify(msg.segment) === JSON.stringify(seg));
    check('publish: nested segment is transcript.v1-valid', !!validateSeg(msg.segment), ajv.errorsText(validateSeg.errors));
  }

  // ── string meetingId (self-host fallback) → channel still well-formed ──
  {
    const { client, pubs } = fakeClient();
    const sink = createRedisTranscriptSink({ client, meetingId: 'abc-defg-hij' });
    await sink.publish(seg);
    check('string-id: channel = tc:meeting:abc-defg-hij:mutable', pubs[0]?.channel === 'tc:meeting:abc-defg-hij:mutable', pubs[0]?.channel);
  }

  // ── Teams/GMeet-compatible live envelope: same-id mutates, distinct pending ids coexist ──
  {
    const { client, pubs } = fakeClient();
    const sink = createRedisTranscriptSink({ client, meetingId: 42, liveEnvelope: 'speaker-snapshot' });
    const draftA = { ...seg, segment_id: 'csrc-201:1000', speaker: '', speaker_key: 'csrc:201', text: 'hello', completed: false };
    const draftAGrown = { ...draftA, text: 'hello world' };
    const draftB = { ...seg, segment_id: 'csrc-201:2000', speaker: '', speaker_key: 'csrc:201', text: 'next thought', completed: false };
    await sink.publish(draftA);
    await sink.publish(draftAGrown);
    await sink.publish(draftB);

    const first = JSON.parse(pubs[0]!.message);
    const grown = JSON.parse(pubs[1]!.message);
    const coexist = JSON.parse(pubs[2]!.message);
    check('snapshot: stable CSRC key is the replacement scope', first.speaker === 'csrc:201', String(first.speaker));
    check('snapshot: same id replaces its draft text', grown.pending.length === 1 && grown.pending[0].text === 'hello world', JSON.stringify(grown.pending));
    check('snapshot: distinct pending ids coexist', coexist.pending.length === 2, JSON.stringify(coexist.pending));

    await sink.publish({ ...draftAGrown, completed: true });
    const confirmed = JSON.parse(pubs[3]!.message);
    check('snapshot: confirmation replaces only its own draft',
      confirmed.confirmed.length === 1 && confirmed.confirmed[0].segment_id === draftA.segment_id
        && confirmed.pending.length === 1 && confirmed.pending[0].segment_id === draftB.segment_id,
      JSON.stringify(confirmed));

    await sink.retract?.([draftB.segment_id]);
    const cleared = JSON.parse(pubs[4]!.message);
    check('snapshot: retract publishes the surviving complete pending set',
      cleared.speaker === 'csrc:201' && cleared.confirmed.length === 0 && cleared.pending.length === 0,
      JSON.stringify(cleared));
  }

  if (failed) { console.error(`\n❌ transcript-redis (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ transcript-redis (L3): XADDs the transcription_segments stream + PUBLISHes tc:meeting:{id}:mutable, payload round-trips a schema-valid transcript.v1 segment.');
}

void main();
