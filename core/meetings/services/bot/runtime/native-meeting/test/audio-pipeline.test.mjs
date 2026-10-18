import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import {setLogger} from '@vexa/gmeet-pipeline';
import {createNativeAudioPipeline} from '../audio-pipeline.mjs';

setLogger(() => {});
const schema = JSON.parse(readFileSync(new URL('../../../../../contracts/transcript.v1/transcript.schema.json', import.meta.url)));
const validate = new Ajv2020({strict: false, formats: {'date-time': true}}).compile({$defs: schema.$defs, $ref: '#/$defs/TranscriptSegment'});

test('overlapping SDK tracks remain separate, carry native keys, flush once and conform to transcript.v1', async () => {
  let listener, stops = 0, finalizes = 0;
  const segments = [], inputs = [];
  const runtime = {
    subscribe(fn) {listener = fn; return () => {listener = undefined;};},
    async startCapture(mode) {assert.equal(mode, 'per-participant');},
    async stopCapture() {stops++;},
  };
  const pipeline = createNativeAudioPipeline(runtime, {
    transcribe: async pcm => {
      const mean = pcm.reduce((a,b) => a+b,0)/pcm.length;
      inputs.push(mean);
      const text = mean > 0 ? 'First participant speaks.' : 'Second participant speaks.';
      return {text, language:'en', duration:1, segments:[{text, start:0, end:1}]};
    },
    sink: {segment: s => segments.push(s), finalize() {finalizes++;}},
  });
  await pipeline.start();
  for (const [userId, name, level] of [[42,'Same name',9000],[57,'Same name',-9000],[99,undefined,9000]]) {
    const pcm = Buffer.alloc(64000);
    for(let i=0;i<pcm.length;i+=2) pcm.writeInt16LE(level,i);
    listener({kind:'audio',pcm,sampleRate:32000,channels:1,ts:2000,userId,speakerName:name,mode:'per-participant'});
  }
  await Promise.all([pipeline.stop(), pipeline.stop()]);
  assert.equal(stops,1); assert.equal(finalizes,1); assert.equal(listener,undefined);
  assert.equal(segments.length,3); assert.equal(inputs.length,3);
  assert.ok(segments.every(s => validate(s)), JSON.stringify(validate.errors));
  const first = segments.find(s=>s.speaker_key.startsWith('sdk-42:'));
  const second = segments.find(s=>s.speaker_key.startsWith('sdk-57:'));
  assert.equal(first.text,'First participant speaks.');
  assert.equal(second.text,'Second participant speaks.');
  assert.equal(first.speaker,second.speaker);
  assert.equal(first.source,undefined); assert.equal(second.source,undefined);
  assert.equal(first.start,second.start);
  assert.equal(segments.find(s=>s.speaker_key.startsWith('sdk-99:')).source,'provisional-cluster-id');
});

test('STT rejection is surfaced and capture still stops and finalizes', async () => {
  let emit, stops=0, finalized=0;
  const fault = Error('stt_unavailable'), errors=[], segments=[];
  const pipeline=createNativeAudioPipeline({
    subscribe(fn) {emit=fn;return ()=>{};}, async startCapture() {}, async stopCapture() {stops++;},
  }, {
    transcribe:async()=>{throw fault;}, onError:e=>errors.push(e),
    sink:{segment:s=>segments.push(s),finalize(){finalized++;}},
  });
  await pipeline.start();
  const pcm=Buffer.alloc(64000);for(let i=0;i<pcm.length;i+=2)pcm.writeInt16LE(9000,i);
  emit({kind:'audio',pcm,sampleRate:32000,channels:1,ts:2000,userId:42,speakerName:'Fixture',mode:'per-participant'});
  await pipeline.stop();
  assert.deepEqual(errors,[fault]);assert.equal(segments.length,0);assert.equal(stops,1);assert.equal(finalized,1);
});
