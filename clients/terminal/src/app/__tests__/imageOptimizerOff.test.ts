/** The terminal runs with Next's image optimizer OFF.
 *
 *  `/_next/image` is on by default in self-hosted Next.js, whether or not the app renders an image,
 *  and its decode path is where GHSA-2xp9-vwfh-vxw4 lives. With `images.unoptimized` the server
 *  answers that route 404 before it loads `sharp` (next/dist/server/next-server.js), so the route
 *  is gone and `sharp` is never required at runtime. The terminal renders no `next/image`, so
 *  nothing is lost; a component that starts using it should be a deliberate decision that turns
 *  this test red first.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import nextConfig from "../../../next.config";

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "__tests__" && name !== "node_modules") sourceFiles(p, acc);
    } else if (/\.(ts|tsx|js|jsx|mjs)$/.test(name)) acc.push(p);
  }
  return acc;
}

describe("Next image optimizer", () => {
  it("is off — /_next/image answers 404 and never loads sharp", () => {
    expect(nextConfig.images?.unoptimized).toBe(true);
  });

  it("is not needed — no source file renders next/image", () => {
    const root = resolve(process.cwd(), "src");
    const users = sourceFiles(root).filter((f) =>
      /from\s+["']next\/(legacy\/)?image["']|require\(\s*["']next\/(legacy\/)?image["']\s*\)/.test(readFileSync(f, "utf8")),
    );
    expect(users.map((f) => f.slice(root.length + 1))).toEqual([]);
  });
});
