import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setLogger} from '@vexa/gmeet-pipeline';
import {TranscriptionClient, setLogger as setWhisperLogger} from '@vexa/transcribe-whisper';
import {createNativeAudioPipeline} from '../audio-pipeline.mjs';

const enabled = process.env.SDK_PCM_FIXTURE && process.env.SDK_EXPECT_WORDS && process.env.VEXA_TX_URL;
test('SDK-format fixture through capture and channel pipeline reaches real STT', {skip: !enabled}, async () => {
  setLogger(() => {}); setWhisperLogger(() => {});
  const pcm = readFileSync(process.env.SDK_PCM_FIXTURE);
  assert.equal(pcm.length % 2, 0);
  let emit;
  const segments = [], errors = [];
  const client = new TranscriptionClient({serviceUrl: process.env.VEXA_TX_URL, apiToken: process.env.VEXA_TX_KEY, maxRetries: 0, requestTimeoutMs: 10000});
  const pipeline = createNativeAudioPipeline({
    subscribe(fn) {emit=fn; return ()=>{};}, async startCapture() {}, async stopCapture() {},
  }, {
    transcribe: (audio,prompt) => client.transcribe(audio,'en',prompt),
    sink: {segment: s=>segments.push(s), finalize() {}}, onError: e=>errors.push(e),
  });
  await pipeline.start();
  try {
    for(let pos=0;pos<pcm.length;pos+=1280) {
      const frame=pcm.subarray(pos,pos+1280);
      emit({kind:'audio',pcm:frame,sampleRate:32000,channels:1,ts:1000+(pos+frame.length)/64,userId:42,speakerName:'Fixture participant',mode:'per-participant'});
    }
  } finally {await pipeline.stop();}
  assert.equal(errors.length,0, errors.map(e=>e.kind??'pipeline_error').join(','));
  assert.ok(segments.length>0,'no transcript');
  const text=segments.map(s=>s.text).join(' ').toLowerCase();
  for(const word of process.env.SDK_EXPECT_WORDS.split(',')) assert.ok(text.includes(word.toLowerCase()),`missing expected fixture word: ${word}`);
  assert.ok(segments.every(s=>s.speaker==='Fixture participant' && s.speaker_key.startsWith('sdk-42:') && s.source===undefined));
});
