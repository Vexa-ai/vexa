/** Founder 2026-10-16 (screenshot): stuck, the home composer folded — send overhung the input, attach,
 *  mic and level vanished — and content showed through beside it. The sticky composer is the SAME
 *  composer (no rule hides a control or changes its size) on a solid full-width bar. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "../desktop/home2/home2.css"), "utf8");
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ sel: m[1].trim(), body: m[2] }));

describe("the sticky home composer", () => {
  it("never folds: no stuck rule hides a control or changes size or padding", () => {
    const stuck = rules.filter((r) => r.sel.includes(".vx-hc[data-stuck]"));
    for (const r of stuck) {
      expect(r.body, r.sel).not.toMatch(/display\s*:\s*none|height\s*:|padding|flex-direction/);
    }
  });
  it("sits on a solid full-width bar in the page background", () => {
    const bar = rules.find((r) => r.sel === ".vx-hc")!;
    expect(bar.body).toMatch(/background\s*:\s*var\(--surface-1\)/);
    expect(bar.body).toMatch(/clip-path\s*:\s*inset\(0 -100vmax/);
    expect(bar.body).toMatch(/box-shadow\s*:\s*0 0 0 100vmax var\(--surface-1\)/);
  });
});
