/**
 * ALLOY: Pause segmentation tests for automatic language re-detection.
 *
 * Break caught: removing pause detection, splitting away from the pause midpoint, or dropping
 * samples would collapse two speech plateaus into one STT request or corrupt merged timestamps.
 *
 * Run: pnpm --filter @vexa/transcribe-whisper exec tsx src/pause-segmenter.test.ts
 */
import { splitOnPauses } from './pause-segmenter.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

const SAMPLE_RATE = 16_000;

function twoSpeechPlateaus(): Float32Array {
  const pcm = new Float32Array(40_000);
  pcm.fill(0.2, 0, 16_000);
  pcm.fill(0.2, 24_000, 40_000);
  return pcm;
}

const ranges = splitOnPauses(twoSpeechPlateaus(), SAMPLE_RATE);
const literalExpected = [
  { startSample: 0, endSample: 20_000 },
  { startSample: 20_000, endSample: 40_000 },
];

check(
  '500 ms pause splits at its midpoint',
  JSON.stringify(ranges) === JSON.stringify(literalExpected),
  `got ${JSON.stringify(ranges)}`,
);
check(
  'ranges preserve every sample exactly once',
  ranges[0]?.startSample === 0
    && ranges.at(-1)?.endSample === 40_000
    && ranges[0]?.endSample === ranges[1]?.startSample,
  `got ${JSON.stringify(ranges)}`,
);

// Break caught: treating leading room silence as an inter-utterance pause would submit a
// silence-only request before the first spoken language and waste one CPU inference.
const leadingSilence = new Float32Array(35_200);
leadingSilence.fill(0.2, 19_200);
const leadingRanges = splitOnPauses(leadingSilence, SAMPLE_RATE);
check(
  'leading silence does not become its own STT range',
  JSON.stringify(leadingRanges) === JSON.stringify([
    { startSample: 0, endSample: 35_200 },
  ]),
  `got ${JSON.stringify(leadingRanges)}`,
);

const shortDip = new Float32Array(36_800);
shortDip.fill(0.2, 0, 16_000);
shortDip.fill(0.2, 20_800);
check(
  '300 ms low-energy dip stays inside one request',
  JSON.stringify(splitOnPauses(shortDip, SAMPLE_RATE)) === JSON.stringify([
    { startSample: 0, endSample: 36_800 },
  ]),
  JSON.stringify(splitOnPauses(shortDip, SAMPLE_RATE)),
);

const allSilence = new Float32Array(32_000);
check(
  'silence-only input stays one range',
  JSON.stringify(splitOnPauses(allSilence, SAMPLE_RATE)) === JSON.stringify([
    { startSample: 0, endSample: 32_000 },
  ]),
  JSON.stringify(splitOnPauses(allSilence, SAMPLE_RATE)),
);

const uninterruptedSpeech = new Float32Array(32_000).fill(0.2);
check(
  'uninterrupted speech stays one range',
  JSON.stringify(splitOnPauses(uninterruptedSpeech, SAMPLE_RATE)) === JSON.stringify([
    { startSample: 0, endSample: 32_000 },
  ]),
  JSON.stringify(splitOnPauses(uninterruptedSpeech, SAMPLE_RATE)),
);

// Break caught: deriving silence from a peak would let two loud transients make the quiet,
// continuous speech between them look like a qualifying pause.
const quietSpeechWithTransients = new Float32Array(48_000).fill(0.01);
quietSpeechWithTransients.fill(0.5, 9_600, 9_920);
quietSpeechWithTransients.fill(0.5, 32_000, 32_320);
check(
  'loud transients do not split quiet continuous speech',
  JSON.stringify(splitOnPauses(quietSpeechWithTransients, SAMPLE_RATE)) === JSON.stringify([
    { startSample: 0, endSample: 48_000 },
  ]),
  JSON.stringify(splitOnPauses(quietSpeechWithTransients, SAMPLE_RATE)),
);

const threePlateaus = new Float32Array(64_000);
threePlateaus.fill(0.2, 0, 16_000);
threePlateaus.fill(0.2, 24_000, 40_000);
threePlateaus.fill(0.2, 48_000, 64_000);
const threeRanges = splitOnPauses(threePlateaus, SAMPLE_RATE);
check(
  'three speech plateaus produce three contiguous ranges',
  JSON.stringify(threeRanges) === JSON.stringify([
    { startSample: 0, endSample: 20_000 },
    { startSample: 20_000, endSample: 44_000 },
    { startSample: 44_000, endSample: 64_000 },
  ]) && threeRanges.reduce((sum, range) => sum + range.endSample - range.startSample, 0) === 64_000,
  JSON.stringify(threeRanges),
);

if (failed) {
  console.error(`\n❌ pause segmenter: ${failed} check(s) failed.`);
  process.exit(1);
}
console.log('\n✅ pause segmenter: qualifying pauses preserve contiguous PCM ranges.');
