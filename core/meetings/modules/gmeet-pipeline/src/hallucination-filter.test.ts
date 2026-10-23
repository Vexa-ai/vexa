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

// #617 — the exact ja/tr "YouTube-outro" hallucinations the reporter saw leak through (the lists were
// en/es/pt/ru only). Each is > 10 chars so no structural rule catches it — only the phrase list does.
// RED at base (returns false → leaks), GREEN at head.
check('ja: "ご視聴ありがとうございました" filtered', isHallucination("ご視聴ありがとうございました") === true);
check('ja: "次の動画でお会いしましょう" filtered', isHallucination("次の動画でお会いしましょう") === true);
check('tr: "Abone olmayı unutmayın" filtered', isHallucination("Abone olmayı unutmayın") === true);
check('tr: case/punctuation-insensitive ("abone olmayı unutmayın.")', isHallucination("abone olmayı unutmayın.") === true);
// Russian stock outros a silent Zoom track produced under forced `ru` (exact, case/punctuation-insensitive).
check('ru: "Спасибо за субтитры!" filtered', isHallucination("Спасибо за субтитры!") === true);
check('ru: "Спасибо за просмотр." filtered', isHallucination("Спасибо за просмотр.") === true);
check("real Russian speech that merely contains the words is kept",
  isHallucination("Спасибо за просмотр документа, давай перейдём к следующему пункту") === false);
check("real Spanish speech still kept (no over-filter)",
  isHallucination("empecemos con el bounded context de facturacion") === false);

// Credit lines with names in them (a real call, 2026-10-14: "Редактор субтитров А.Семкин Корректор
// А.Егорова" mid-meeting). Never speech, whatever the audio did.
check('ru credits with names → dropped', isHallucination("Редактор субтитров А.Семкин Корректор А.Егорова", { voiced: true }) === true);
check('ru "Субтитры сделал <name>" → dropped', isHallucination("Субтитры сделал Иван Петров", { voiced: true }) === true);
check('ru "Субтитры подготовила <name>" → dropped', isHallucination("Субтитры подготовила М. Сидорова") === true);
check('en "Subtitles by <name>" → dropped', isHallucination("Subtitles by the Amara.org community", { voiced: true }) === true);
// Outros: dropped on a window with no speech energy, kept when somebody actually said it.
check('"Thanks for watching, see you!" on a silent window → dropped', isHallucination("Thanks for watching, see you!", { voiced: false }) === true);
check('"Спасибо за внимание" on a silent window → dropped', isHallucination("Спасибо за внимание.", { voiced: false }) === true);
check('"Спасибо за внимание" said aloud → kept', isHallucination("Спасибо за внимание.", { voiced: true }) === false);
check("speech that talks about subtitles is kept",
  isHallucination("Давай включим субтитры на этом видео для гостей", { voiced: true }) === false);
check("speech about an editor is kept",
  isHallucination("Редактор сказал, что статья выйдет в пятницу", { voiced: true }) === false);

if (failed) { console.error(`\n❌ hallucination-filter: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ hallucination-filter: all checks pass — phrase-list + short/repetition junk dropped, real speech kept.`);
