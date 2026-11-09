/**
 * createGmeetPageCapture: the v1 producer's chunks reach PageCapture.onAudio unchanged (channel, name or null,
 * pcm, ts); stop is final. The producer is faked; its own tests cover the glow binding.
 *   npx tsx src/page-capture.test.ts
 */
import { createGmeetPageCapture } from './page-capture.js';

let failed = 0;
const check = (n: string, ok: boolean, d = '') => { console.log(`  ${ok ? '✅' : '❌'} ${n}${ok ? '' : '  — ' + d}`); if (!ok) failed++; };

let sink: any = null, stopped = 0, started = 0, selfSeen: string | undefined;
const got: any[] = []; const obs: any[] = []; const hints: any[] = [];
const cap = createGmeetPageCapture({
  selfName: 'Dmitry',
  onAudio: (ch, name, pcm, ts) => got.push({ ch, name, n: pcm.length, ts }),
  observe: (_k, d) => obs.push(d),
  onSpeakerHint: (n, t, e) => hints.push({ n, t, e }),
  makeV1: (o: any) => { sink = o.sink; selfSeen = o.selfName; return { start: async () => { started++; }, stop: () => { stopped++; }, streamCount: () => 3 }; },
});
await Promise.resolve(); await Promise.resolve();
check('the producer is started once, with the local name excluded', started === 1 && selfSeen === 'Dmitry');
check('its start is observed as data', obs.some((o) => o.type === 'pagecapture-started' && o.platform === 'google_meet' && o.channels === 3), JSON.stringify(obs));
sink.audioChunk({ speakerId: 'spk-1', speakerIndex: 1, samples: new Float32Array(4096), ts: 1_700_000_000_000, speakerName: 'Maya' });
sink.audioChunk({ speakerId: 'spk-0', speakerIndex: 0, samples: new Float32Array(2048), ts: 1_700_000_000_256 });
check('a lit-name chunk crosses with its channel and name', got[0]?.ch === 1 && got[0]?.name === 'Maya' && got[0]?.n === 4096 && got[0]?.ts === 1_700_000_000_000, JSON.stringify(got[0]));
check('an ambiguous chunk crosses with name null', got[1]?.ch === 0 && got[1]?.name === null, JSON.stringify(got[1]));
check('channels come from the producer', cap.channels() === 3);
sink.event({ kind: 'active-speaker', ts: 1_700_000_000_300, speaker: 'Maya', detail: { hint: 'dom-active', isEnd: false } });
sink.event({ kind: 'active-speaker', ts: 1_700_000_000_900, speaker: 'Maya', detail: { hint: 'dom-active', isEnd: true } });
check('active-speaker edges reach the host as hints', JSON.stringify(hints) === JSON.stringify([{ n: 'Maya', t: 1_700_000_000_300, e: false }, { n: 'Maya', t: 1_700_000_000_900, e: true }]), JSON.stringify(hints));
cap.stop(); cap.stop();
sink.audioChunk({ speakerId: 'spk-2', speakerIndex: 2, samples: new Float32Array(10), ts: 1, speakerName: 'Late' });
check('stop is final: the producer stopped once, nothing crosses after', stopped === 1 && got.length === 2);

console.log(failed ? `\n${failed} FAILED` : '\ngmeet page-capture: all passed');
process.exit(failed ? 1 : 0);
