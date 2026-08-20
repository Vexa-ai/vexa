import assert from 'node:assert/strict';

import { createVoiceActHandler } from './voice-act-handler.js';

const calls: unknown[][] = [];
const handle = createVoiceActHandler({
  async speak(text, voice) { calls.push(['speak', text, voice]); },
  async speakAudio(audioBase64) { calls.push(['audio', audioBase64]); },
  async stop() { calls.push(['stop']); },
});

await handle({ action: 'speak', text: 'hello', voice: 'alloy' });
await handle({ action: 'speak_audio', audioBase64: 'UklGRg==' });
await handle({ action: 'speak_stop' });
await handle({ action: 'leave' });

assert.deepEqual(calls, [
  ['speak', 'hello', 'alloy'],
  ['audio', 'UklGRg=='],
  ['stop'],
]);
console.log('voice-act-handler: ok');
