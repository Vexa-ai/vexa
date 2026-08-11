/**
 * hallucinations-are-subset — the mixed lane's phrase list is a SELECTION from the gmeet
 * lane's, never a fork that drifts.
 *
 * The two lanes cannot import each other (gate:isolation), so the relationship is pinned by
 * reading the files. Every exact phrase the mixed lane suppresses must exist in a gmeet list;
 * the reverse is deliberately false — the gmeet list carries "Yeah." and "I don't know.",
 * which this lane must never suppress. Regex lines (`re:`) are the lane's own generalisation
 * of the credit-string family and are exempt.
 *
 * Skips itself when the gmeet package is not on disk (published/dist context).
 *
 *   tsx src/hallucinations-are-subset.test.ts
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const MINE = resolve(here, 'hallucinations');
const THEIRS = resolve(here, '..', '..', 'gmeet-pipeline', 'src', 'hallucinations');

if (!existsSync(THEIRS)) {
  console.log('⏭  hallucinations-are-subset: gmeet-pipeline not on disk — skipped.');
  process.exit(0);
}

const read = (dir: string): Set<string> => {
  const out = new Set<string>();
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.txt')) continue;
    for (const line of readFileSync(join(dir, f), 'utf8').split('\n')) {
      const t = line.trim();
      if (t && !t.startsWith('#') && !t.startsWith('re:')) out.add(t.toLowerCase());
    }
  }
  return out;
};

const mine = read(MINE);
const theirs = read(THEIRS);
const orphans = [...mine].filter((p) => !theirs.has(p));

console.log(`  mixed lane: ${mine.size} exact phrase(s); gmeet lane: ${theirs.size}`);
if (orphans.length) {
  console.error(`❌ hallucinations-are-subset: ${orphans.length} phrase(s) not present in the gmeet lists:\n  ` +
    orphans.map((o) => JSON.stringify(o)).join('\n  '));
  process.exit(1);
}
const risky = ['yeah.', 'yes.', 'okay.', 'ok.', 'no.', "i don't know.", 'thank you.', 'thanks.'];
const adopted = risky.filter((r) => mine.has(r));
if (adopted.length) {
  console.error(`❌ hallucinations-are-subset: the mixed lane adopted phrases that are real meeting speech: ${adopted.join(', ')}`);
  process.exit(1);
}
console.log(`  ${risky.length} known-risky gmeet phrase(s) correctly NOT adopted`);
console.log('\n✅ hallucinations-are-subset: the mixed list is a strict, meeting-safe subset of the gmeet lists.');
