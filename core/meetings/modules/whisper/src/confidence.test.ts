/**
 * stt.v1 low-confidence filter golden — pins isLowConfidenceSegment, the single
 * chokepoint that drops acoustically-junk faster-whisper segments before they reach
 * the confirm loop. Pure + offline (the HTTP client is exercised by pipeline replay
 * + live L4). Run: npm test  (or npx tsx src/confidence.test.ts)
 */
import { isLowConfidenceSegment } from "./index.js";

let failed = 0;
const check = (name: string, cond: boolean) => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}`);
  if (!cond) failed++;
};

// Kept (real speech)
check("clean segment kept", isLowConfidenceSegment({ avg_logprob: -0.3, no_speech_prob: 0.1, compression_ratio: 1.5 }) === false);
check("empty segment kept (nothing to score)", isLowConfidenceSegment({}) === false);
check("high no_speech alone kept (logprob ok)", isLowConfidenceSegment({ avg_logprob: -0.5, no_speech_prob: 0.9 }) === false);

// Dropped (junk) — the three independent rules
check("high no_speech + low logprob dropped", isLowConfidenceSegment({ avg_logprob: -1.1, no_speech_prob: 0.7 }) === true);
check("high compression ratio dropped", isLowConfidenceSegment({ compression_ratio: 2.5 }) === true);
check("very low logprob dropped", isLowConfidenceSegment({ avg_logprob: -1.4 }) === true);

// Boundaries (strict comparisons — the threshold itself is kept)
check("logprob boundary -1.3 kept (strict <)", isLowConfidenceSegment({ avg_logprob: -1.3 }) === false);
check("compression boundary 2.4 kept (strict >)", isLowConfidenceSegment({ compression_ratio: 2.4 }) === false);

if (failed) { console.error(`\n❌ confidence: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ confidence: all checks pass — junk segments dropped, real speech and threshold edges kept.`);
