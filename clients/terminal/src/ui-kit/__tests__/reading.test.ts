/** READING (founder 2026-10-15: "better and comfy readability for chat and pages"): the reading
 *  stylesheet is part of the kit, reads only tokens, and sets the column, the body and the chips. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const KIT = join(__dirname, "..");
const css = readFileSync(join(KIT, "primitives", "reading.css"), "utf8");
const tokens = readFileSync(join(KIT, "..", "app", "tokens.css"), "utf8");

describe("reading typography", () => {
  it("is loaded with the kit, after prose", () => {
    const kit = readFileSync(join(KIT, "styles.css"), "utf8");
    expect(kit.indexOf("reading.css")).toBeGreaterThan(kit.indexOf("prose.css"));
  });
  it("a ~70ch column, 16px at 1.6, a softer body colour in both themes — all tokens", () => {
    expect(tokens).toMatch(/--read-size: 16px;\s+--read-leading: 1\.6;\s+--read-measure: 70ch;/);
    expect(tokens.match(/--text-body:/g)).toHaveLength(2);
    expect(css).toMatch(/\.vx-read-col \{ max-width: calc\(var\(--read-measure\)/);
    expect(css).not.toMatch(/#[0-9a-f]{3,6}\b|rgba?\(/i);
  });
  it("chips sit on the baseline and never make a line taller", () => {
    expect(css).toMatch(/\.vx-prose \.vx-inline-chip[^{]*\{[^}]*line-height: 1\.25;[^}]*vertical-align: baseline;[^}]*border-width: 0;/);
  });
});
