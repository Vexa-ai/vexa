/**
 * Hallucination-filter golden — pins the phrase-list + structural junk rules.
 * Run: npm test  (chained)  or  npx tsx src/hallucination-filter.test.ts
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { isHallucination } from "./index.js";

const here = dirname(fileURLToPath(import.meta.url));

let failed = 0;
const check = (name: string, cond: boolean) => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}`);
  if (!cond) failed++;
};

// Structural rules (deterministic, no list dependency)
check("empty → dropped", isHallucination("") === true);
check("whitespace → dropped", isHallucination("   ") === true);
check("short single word → dropped", isHallucination("ok") === true);
check("long single word kept", isHallucination("internationalization") === false);
check("clean sentence kept", isHallucination("the quick brown fox jumps over") === false);
check("repetition loop (3+ ×) → dropped", isHallucination("i love it i love it i love it i love it") === true);

// Phrase-list path: a phrase actually in en.txt must be filtered (loaded the same way the brick loads).
const enPhrases = readFileSync(resolve(here, "hallucinations", "en.txt"), "utf-8")
  .split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
check(`en.txt loaded (${enPhrases.length} phrases)`, enPhrases.length > 0);
check(`a known list phrase is filtered ("${enPhrases[0]}")`, isHallucination(enPhrases[0]) === true);

if (failed) { console.error(`\n❌ hallucination-filter: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ hallucination-filter: all checks pass — phrase-list + short/repetition junk dropped, real speech kept.`);
