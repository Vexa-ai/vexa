import {
  spanFitsCsrcOwnership,
  TeamsCsrcGmeetPipeline,
  type TeamsCsrcTranscriptSegment,
} from './teams-csrc-gmeet-pipeline.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

let now = 0;
let calls = 0;
const prompts: Array<string | undefined> = [];
const triggers: string[] = [];
const segments: TeamsCsrcTranscriptSegment[] = [];
const durable = new Map<string, TeamsCsrcTranscriptSegment>();
const pipeline = new TeamsCsrcGmeetPipeline({
  lookbackMs: 0,
  flickerHoldMs: 0,
  onsetGapMs: 1000,
  buffer: { scheduleSubmissions: false, now: () => now, silenceRmsThreshold: 0 },
  onSegment: (segment) => {
    segments.push(segment);
    if (!segment.completed && !segment.text.trim()) durable.delete(segment.segmentId);
    else durable.set(segment.segmentId, segment);
  },
  transcribe: async (_audio, prompt, context) => {
    prompts.push(prompt);
    triggers.push(context?.trigger ?? 'missing');
    calls++;
    const text = calls === 1 ? 'hello world changing' : calls === 2 ? 'hello world stable tail' : 'next phrase';
    return {
      text,
      language: 'en',
      duration: 4,
      segments: calls <= 2
        ? [
            { text: 'hello world', start: 0, end: 1.5 } as any,
            { text: calls === 1 ? 'changing' : 'stable tail', start: 1.5, end: 4 } as any,
          ]
        : [{ text, start: 0, end: 2 } as any],
    };
  },
});

check('a transcript extending far past a tiny CSRC interval is rejected',
  !spanFitsCsrcOwnership([{ startMs: 18_211, endMs: 19_053 }], 18_301, 19_837));
check('the tiny flicker still fails with the Teams ownership tolerances',
  !spanFitsCsrcOwnership([{ startMs: 18_211, endMs: 19_053 }], 18_301, 19_837, 1200, 600));
check('a 951ms Teams activation lag is accepted without widening the routed-audio lookback',
  spanFitsCsrcOwnership([{ startMs: 110_951 }], 110_000, 120_788, 1200));
check('a 565ms Whisper end overhang is accepted for an otherwise owned turn',
  spanFitsCsrcOwnership([{ startMs: 110_951, endMs: 123_551 }], 110_000, 124_116, 1200, 600));
check('short false/true gaps are one continuous ownership interval',
  spanFitsCsrcOwnership([
    { startMs: 65_141, endMs: 67_089 },
    { startMs: 67_248, endMs: 82_650 },
  ], 66_675, 82_971));

pipeline.recordTransportEvent({ csrc: 201, active: true, tMs: 0 });
for (let index = 0; index < 4; index++) {
  now = index * 500;
  pipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), now);
}
now = 2000;
await pipeline.requestTranscription(201);
for (let index = 4; index < 8; index++) {
  now = index * 500;
  pipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), now);
}
now = 4000;
await pipeline.requestTranscription(201);

const confirmed = segments.filter((segment) => segment.completed && segment.text);
check('shared GMeet window confirms the stable complete leading segment',
  confirmed.some((segment) => segment.csrc === 201 && segment.text === 'hello world'),
  JSON.stringify(segments));
check('first submission has no prompt', prompts[0] === undefined, JSON.stringify(prompts));
check('ordinary Teams requests enter Whisper only through the shared interval seam',
  triggers.slice(0, 2).every((trigger) => trigger === 'scheduled')
    && !triggers.includes('buffer'),
  JSON.stringify(triggers));

pipeline.recordTransportEvent({ csrc: 201, active: false, tMs: 4000 });
pipeline.recordTransportEvent({ csrc: 201, active: true, tMs: 4500 });
now = 4500;
pipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), now);
check('sub-second false/true flicker keeps the same virtual turn', pipeline.health().turns === 1,
  JSON.stringify(pipeline.health()));

pipeline.recordTransportEvent({ csrc: 201, active: false, tMs: 5000 });
pipeline.recordTransportEvent({ csrc: 201, active: true, tMs: 6501 });
now = 6501;
pipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), now);
check('a real gap opens a new virtual turn', pipeline.health().turns === 2,
  JSON.stringify(pipeline.health()));

await pipeline.dispose();
check('pipeline settles every Whisper request on dispose', pipeline.health().inFlight === 0,
  JSON.stringify(pipeline.health()));
check('confirmed rows replace GMeet drafts under the same transcript key',
  [...durable.values()].every((segment) => segment.completed), JSON.stringify([...durable.values()]));

// m26123 regression: a genuine tail after a repeated acknowledgement was classified as a
// repetition loop on every growing pass. LocalAgreement never got a usable candidate and the
// whole 20-second turn disappeared. The Teams adapter must keep the clean draft, and the
// terminal timeout/flush must promote it even without a second confirming pass.
let timeoutNow = 0;
let timeoutCalls = 0;
const timeoutSegments: TeamsCsrcTranscriptSegment[] = [];
const timeoutBase = 'good that sounds good that sounds good that sounds good that sounds good well sorry we have been chaotic recently because it has been too busy here with bits and pieces but we are back now';
const timeoutPipeline = new TeamsCsrcGmeetPipeline({
  lookbackMs: 0,
  flickerHoldMs: 0,
  onsetGapMs: 1000,
  buffer: { scheduleSubmissions: false, now: () => timeoutNow, silenceRmsThreshold: 0 },
  onSegment: (segment) => timeoutSegments.push(segment),
  transcribe: async () => {
    timeoutCalls++;
    const text = timeoutCalls === 1 ? timeoutBase : `${timeoutBase} and ready to continue`;
    return {
      text,
      language: 'en',
      duration: 4,
      segments: [{ text, start: 0, end: 4 } as any],
    };
  },
});
timeoutPipeline.recordTransportEvent({ csrc: 840, active: true, tMs: 0 });
for (let index = 0; index < 8; index++) {
  timeoutNow = index * 500;
  timeoutPipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), timeoutNow);
}
await timeoutPipeline.requestTranscription(840);
timeoutNow = 4001;
timeoutPipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), timeoutNow);
check('four-second timeout promotes the last clean draft without waiting for turn close',
  timeoutSegments.some((segment) => segment.completed && segment.text.includes('bits and pieces but we are back now')),
  JSON.stringify(timeoutSegments));
timeoutNow = 5500;
await timeoutPipeline.requestTranscription(840);
check('a timeout-promoted row continues growing as completed without reverting to draft',
  timeoutSegments.some((segment) => segment.completed && segment.text.endsWith('and ready to continue'))
    && !timeoutSegments.some((segment) => !segment.completed && segment.text.endsWith('and ready to continue')),
  JSON.stringify(timeoutSegments));
timeoutPipeline.recordTransportEvent({ csrc: 840, active: false, tMs: 4000 });
await timeoutPipeline.flush();
check('timeout/terminal flush promotes the last clean draft when LocalAgreement never confirms',
  timeoutSegments.some((segment) => segment.completed && segment.text.includes('bits and pieces but we are back now')),
  JSON.stringify(timeoutSegments));
check('the timeout promotion is observable exactly once', timeoutPipeline.health().timeoutPromotions === 1,
  JSON.stringify(timeoutPipeline.health()));
await timeoutPipeline.dispose();

let boundedNow = 0;
const boundedSegments: TeamsCsrcTranscriptSegment[] = [];
const boundedPipeline = new TeamsCsrcGmeetPipeline({
  lookbackMs: 0,
  flickerHoldMs: 0,
  buffer: { scheduleSubmissions: false, now: () => boundedNow, silenceRmsThreshold: 0 },
  onSegment: (segment) => boundedSegments.push(segment),
  transcribe: async () => ({
    text: 'terminal overhang',
    language: 'en',
    duration: 4,
    segments: [{ text: 'terminal overhang', start: 0, end: 4 } as any],
  }),
});
boundedPipeline.recordTransportEvent({ csrc: 414, active: true, tMs: 0 });
for (let index = 0; index < 4; index++) {
  boundedNow = index * 500;
  boundedPipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), boundedNow);
}
boundedNow = 2000;
await boundedPipeline.requestTranscription(414);
boundedNow = 4000;
await boundedPipeline.requestTranscription(414);
check('published Whisper timestamps never extend past the source\'s last routed PCM sample',
  boundedSegments.some((segment) => segment.completed
    && segment.text === 'terminal overhang'
    && segment.endMs === 2000),
  JSON.stringify(boundedSegments));
await boundedPipeline.dispose();

if (failed > 0) process.exit(1);
console.log('\n✅ Teams CSRC lanes drive the shared GMeet window without Pyannote.');
