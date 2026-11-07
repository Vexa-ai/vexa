// Owned-meeting probe. Prints counters only; no audio, names or transcript text.
import {createSdkJoinSession} from '@vexa/join/node';
import {setLogger} from '@vexa/gmeet-pipeline';
import {TranscriptionClient, setLogger as setWhisperLogger} from '@vexa/transcribe-whisper';
import {createNativeMeetingRuntime} from './session.mjs';
import {createNativeAudioPipeline} from './audio-pipeline.mjs';

setLogger(() => {});
setWhisperLogger(() => {});
let input = '';
for await (const part of process.stdin) {
  input += part;
  if (input.length > 65536) throw Error('input_too_large');
}
let runtime, joined, audio, failed = false, calls = 0, segments = 0, named = 0;
const speakers = new Set();
try {
  if (!process.env.VEXA_TX_URL) throw Error('stt_url_required');
  const client = new TranscriptionClient({serviceUrl: process.env.VEXA_TX_URL,
    apiToken: process.env.VEXA_TX_KEY, maxRetries: 0, requestTimeoutMs: 10000});
  runtime = createNativeMeetingRuntime({sdkDir: process.env.ZOOM_SDK_DIR, addonPath: process.env.ZOOM_SDK_ADDON});
  joined = createSdkJoinSession(runtime, JSON.parse(input), {
    timeoutMs: 60000, onState: state => console.log(JSON.stringify(state)),
  });
  await joined.admitted;
  audio = createNativeAudioPipeline(runtime, {
    transcribe: (pcm, prompt) => {calls++; return client.transcribe(pcm, 'en', prompt);},
    sink: {segment(segment) {
      segments++;
      if (segment.speaker !== 'Speaker') named++;
      speakers.add(segment.speaker_key.split(':')[0]);
    }, finalize() {}},
    onState: state => console.log(JSON.stringify({capture: state})),
    onError: error => {failed = true; console.log(JSON.stringify({audioFailure: typeof error === 'string' ? error : error.kind ?? 'pipeline_failed'}));},
    noAudioMs: 40000,
  });
  await audio.start();
  await new Promise(resolve => setTimeout(resolve, 30000));
  await audio.stop();
  console.log(JSON.stringify({receipt: 'transcription_counters', calls, segments, named, speakers: speakers.size, failed}));
  if (!segments || failed) process.exitCode = 1;
} catch (error) {
  console.log(JSON.stringify({failure: error.code ?? error.message})); process.exitCode = 1;
} finally {
  try {await audio?.stop();}
  finally {try {await joined?.leave();}
    finally {if (runtime) console.log(JSON.stringify({receipt: 'runtime_closed', ...await runtime.dispose()}));}}
}
