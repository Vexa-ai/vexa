import {createSdkCapture} from '@vexa/zoom-sdk-capture';
import {createGmeetPipeline} from '@vexa/gmeet-pipeline';

/** Bot composition: native participant capture into the shared per-channel STT engine.
 * The caller owns join/leave/runtime disposal and injects STT and transcript storage.
 */
export function createNativeAudioPipeline(runtime, {transcribe, sink, config, onError, onState, noAudioMs}) {
  const identities = new Map();
  const pipeline = createGmeetPipeline({
    transcribe, sink, config, onError,
    // transcript.v1 has no SDK source enum. Omit it instead of claiming browser
    // glow attribution; speaker_key carries the native meeting-scoped identity.
    speakerSource: null,
    channelKey: channel => identities.get(channel),
  });
  const capture = createSdkCapture(runtime, {
    audioChunk(frame) {
      identities.set(frame.speakerIndex, frame.speakerId);
      pipeline.feedAudio(frame.speakerIndex, frame.speakerName, frame.samples, frame.ts);
    },
    event() {},
    finalize: () => pipeline.dispose(),
  }, {mode: 'per-participant', onError, onState, noAudioMs});
  return {start: () => capture.start(), stop: () => capture.stop()};
}
