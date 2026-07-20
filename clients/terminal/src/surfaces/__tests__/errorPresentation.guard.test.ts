/** Grep-guard (issue #533 C3): no surface renders the raw error idiom
 *  `e instanceof Error ? e.message : String(e)` — surfaces render `presentError(e)`, never
 *  `e.message`. This is the terminal twin of #508's C2 pattern: the 46th site cannot land
 *  silently. The one legal home for raw plumbing is the presenter itself (apiClient.ts).
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SURFACES_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
// The raw idiom in any spelling of the binding: `x instanceof Error ? x.message : String(x)`.
const RAW_IDIOM = /(\w+) instanceof Error \? \1\.message : String\(\1\)/;

describe("error presentation guard", () => {
  it("no surface file contains the raw error-render idiom", () => {
    const offenders: string[] = [];
    for (const f of readdirSync(SURFACES_DIR)) {
      if (!/\.(ts|tsx)$/.test(f)) continue;
      const src = readFileSync(join(SURFACES_DIR, f), "utf8");
      const m = src.match(RAW_IDIOM);
      if (m) offenders.push(`${f}: ${m[0]}`);
    }
    expect(offenders).toEqual([]);
  });
});
