/** Every terminal source file is text: no raw NUL byte. git treats a file with one as binary, so
 *  its diff cannot be read in review. A NUL a program needs is written as the `\u0000` escape. */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "node_modules") sourceFiles(p, acc);
    } else if (/\.(ts|tsx|js|jsx|mjs|css)$/.test(name)) acc.push(p);
  }
  return acc;
}

describe("terminal sources", () => {
  it("carry no raw NUL byte", () => {
    const root = resolve(__dirname, "../..");
    const withNul = sourceFiles(root).filter((p) => readFileSync(p).includes(0));
    expect(withNul).toEqual([]);
  });
});
