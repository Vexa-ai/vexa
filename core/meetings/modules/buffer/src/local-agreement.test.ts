/**
 * The confirm-loop golden — pins LocalAgreement-N (the shared confirmation core).
 * Moved down from the pipeline now that the engine lives here (@vexa/transcribe-buffer).
 * Run: npm test  (or npx tsx src/local-agreement.test.ts)
 */
import {
  words, longestCommonWordPrefix, commonWordPrefix, localAgreement,
  type AgreementSegment,
} from "./index.js";

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};

// ── word helpers ────────────────────────────────────────────────────────────
check("words() splits + trims", JSON.stringify(words("  hello   world ")) === JSON.stringify(["hello", "world"]));
check("longestCommonWordPrefix", longestCommonWordPrefix(["a", "b", "c"], ["a", "b", "x"]) === 2);
check("commonWordPrefix across N passes", commonWordPrefix([["a", "b", "c"], ["a", "b", "x"], ["a", "b", "y"]]) === 2);
check("commonWordPrefix empty → 0", commonWordPrefix([]) === 0);
check("commonWordPrefix single pass → full", commonWordPrefix([["a", "b"]]) === 2);

// ── LocalAgreement-N: need `agree` consecutive passes before anything confirms ──
{
  const r = localAgreement([{ text: "hello world", endMs: 1000 }], [], 2000, false, 3);
  check("fewer than `agree` passes → nothing confirms, history carried",
    r.confirmCount === 0 && r.history.length === 1, JSON.stringify(r));
}

// ── confirm a WHOLE stable leading segment once 3 passes agree on its prefix ────
{
  const segments: AgreementSegment[] = [
    { text: "hello world", endMs: 1000 },   // stable across all 3 passes → confirms
    { text: "bar baz", endMs: 1500 },        // still-forming tail → stays pending
  ];
  const history = [["hello", "world", "foo", "qux"], ["hello", "world", "zzz"]]; // prior 2 passes
  const r = localAgreement(segments, history, 2000, false, 3);
  check("3 agreeing passes confirm the whole leading segment (never partial)",
    r.confirmCount === 1 && r.history.length === 0, JSON.stringify(r));
}

// ── closing → everything confirms (last chance) ────────────────────────────────
{
  const r = localAgreement([{ text: "a", endMs: 1 }, { text: "b", endMs: 2 }], [["x"]], 5000, true, 3);
  check("closing confirms all segments", r.confirmCount === 2, JSON.stringify(r));
}

// ── window guard: never confirm a segment overrunning the read audio window ─────
{
  const segments: AgreementSegment[] = [
    { text: "hello world", endMs: 9999 },   // prefix-stable BUT past the read window
    { text: "bar baz", endMs: 9999 },
  ];
  const history = [["hello", "world", "foo"], ["hello", "world", "zzz"]];
  const r = localAgreement(segments, history, 1000, false, 3);  // spanEndMs 1000 ⇒ 9999 overruns
  check("segment past the read window is held (not confirmed)", r.confirmCount === 0, JSON.stringify(r));
}

if (failed) { console.error(`\n❌ local-agreement: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ local-agreement: all checks pass — LocalAgreement-N confirms only whole, window-bounded, N-stable prefixes.`);
