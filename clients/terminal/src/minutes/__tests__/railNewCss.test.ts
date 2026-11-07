/** Founder 2026-10-16: "+ New" wrapped onto two lines as a heavy orange block. It is one line, a quiet
 *  outline, ~28px high, centred in the header. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "../../ui-kit/layout/layout.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const rail = readFileSync(join(__dirname, "../Rail.tsx"), "utf8");

describe("the sidebar + New", () => {
  it("is one quiet line", () => {
    const rule = /\.vx-rail-new\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(rule).toMatch(/white-space\s*:\s*nowrap/);
    expect(rule).toMatch(/height\s*:\s*28px/);
    expect(rule).toMatch(/background\s*:\s*transparent/);
    expect(rule).toMatch(/border\s*:\s*1px solid/);
    expect(rule).toMatch(/align-self\s*:\s*center/);
    expect(rail).toMatch(/className="vx-rail-new"/);
    expect(rail).not.toMatch(/variant="primary" data-rail-act="home"/);
  });
});
