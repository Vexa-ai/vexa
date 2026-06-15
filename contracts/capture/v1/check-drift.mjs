/**
 * capture.v1 drift gate — the brick copies MUST be byte-identical to canonical.
 * One contract, copied into bricks only because isolation forbids reaching into
 * /contracts; this gate makes the copies provably the same source.
 *
 *   node contracts/capture/v1/check-drift.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const canonical = readFileSync(join(root, 'contracts/capture/v1/schema.ts'), 'utf8');
const copies = [
  'modules/capture/src/contract/capture-v1.ts',
  'modules/recorder/src/contracts/capture-v1.ts',
];

let bad = 0;
for (const rel of copies) {
  const got = readFileSync(join(root, rel), 'utf8');
  if (got === canonical) { console.log(`  ✅ ${rel}`); }
  else { console.error(`  ❌ ${rel} — DRIFTED from contracts/capture/v1/schema.ts`); bad++; }
}
if (bad) { console.error(`\n❌ ${bad} capture.v1 copy(ies) drifted — re-sync from canonical.`); process.exit(1); }
console.log('\n✅ capture.v1: all copies match canonical');
