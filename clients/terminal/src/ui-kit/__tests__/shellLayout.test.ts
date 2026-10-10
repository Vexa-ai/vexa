/** The shell's layout over a WIDTH SWEEP (guidelines §3.1, Phase 1 acceptance for 1d).
 *
 *  The baseline this replaces, measured on app.dev on 2026-10-10 with the pages panel open:
 *  1440 → chat 960 · 1024 → 544 · 820 → 340 · 640 → 160, with both side panes holding 240px at every
 *  width. The conversation absorbed every lost pixel. Each block below states the rule that turns
 *  that around, and the sweep asserts it at every width from 320 to 2560. */
import { describe, expect, it } from "vitest";
import { BREAKPOINTS, DEFAULT_PREFS, EDGE_W, MODES, STRIP_W, shellLayout, shellMode, type ShellPrefs } from "../layout/shellLayout";

const SWEEP = Array.from({ length: (2560 - 320) / 16 + 1 }, (_, i) => 320 + i * 16);
const ALL_PREFS: ShellPrefs[] = [
  { railOpen: true, pagesOpen: true }, { railOpen: false, pagesOpen: true },
  { railOpen: true, pagesOpen: false }, { railOpen: false, pagesOpen: false },
];

describe("modes", () => {
  it("map the breakpoints exactly", () => {
    expect(shellMode(1920)).toBe("wide");
    expect(shellMode(BREAKPOINTS.wide)).toBe("wide");
    expect(shellMode(BREAKPOINTS.wide - 1)).toBe("desktop");
    expect(shellMode(BREAKPOINTS.desktop)).toBe("desktop");
    expect(shellMode(BREAKPOINTS.desktop - 1)).toBe("compact");
    expect(shellMode(BREAKPOINTS.compact)).toBe("compact");
    expect(shellMode(BREAKPOINTS.compact - 1)).toBe("narrow");
    expect(shellMode(BREAKPOINTS.narrow)).toBe("narrow");
    expect(shellMode(BREAKPOINTS.narrow - 1)).toBe("single");
  });
});

describe("the conversation is protected — swept 320…2560, every preference", () => {
  for (const prefs of ALL_PREFS) {
    it(`never below the mode's floor (rail ${prefs.railOpen ? "open" : "closed"}, pages ${prefs.pagesOpen ? "open" : "closed"})`, () => {
      for (const vw of SWEEP) {
        const L = shellLayout(vw, prefs);
        expect(L.widths.rail + L.widths.chat + L.widths.pages, `columns add up at ${vw}`).toBe(vw);
        expect(L.widths.chat, `chat ≥ ${L.chatMin} at ${vw}`).toBeGreaterThanOrEqual(L.chatMin);
        // Where the conversation has no floor (narrow, single) it takes the whole width but the strip.
        if (L.chatMin === 0) expect(L.widths.chat).toBe(vw - L.widths.rail);
      }
    });
  }

  it("the audit's four widths, before → after (pages panel open)", () => {
    // before: 960 · 544 · 340 · 160
    expect(shellLayout(1440).widths.chat).toBeGreaterThanOrEqual(560);
    expect(shellLayout(1024).widths.chat).toBeGreaterThanOrEqual(440);
    expect(shellLayout(820).widths.chat).toBe(820 - STRIP_W);
    expect(shellLayout(640).widths.chat).toBe(640);
  });
});

describe("collapse order is fixed: the rail goes first, then the pages panel", () => {
  it("the rail is a strip before the pages panel stops docking", () => {
    for (const vw of SWEEP) {
      const L = shellLayout(vw);
      if (L.pagesKind !== "docked") expect(L.railKind, `at ${vw}`).not.toBe("docked");
    }
  });

  it("wide and desktop dock both; compact strips the rail; narrow sheets the pages; single drawers both", () => {
    expect(shellLayout(1920)).toMatchObject({ railKind: "docked", pagesKind: "docked" });
    expect(shellLayout(1280)).toMatchObject({ railKind: "docked", pagesKind: "docked" });
    expect(shellLayout(1024)).toMatchObject({ railKind: "strip", pagesKind: "docked" });
    expect(shellLayout(820)).toMatchObject({ railKind: "strip", pagesKind: "sheet" });
    expect(shellLayout(640)).toMatchObject({ railKind: "drawer", pagesKind: "fullscreen" });
  });

  it("acceptance row: at 820 with both panes open the conversation is ≥ 440 and the rail is the 48px strip", () => {
    const L = shellLayout(820, { railOpen: true, pagesOpen: true });
    expect(L.widths.rail).toBe(48);
    expect(L.widths.chat).toBeGreaterThanOrEqual(440);
  });

  it("a sheet is at most 480 and 90% of the window; single mode covers it", () => {
    expect(shellLayout(820).widths.sheet).toBe(480);
    expect(shellLayout(740).widths.sheet).toBe(480);
    expect(shellLayout(640).widths.sheet).toBe(640);
  });
});

describe("preference and fit are separate state", () => {
  it("a closed pane folds in docked modes, and the reader's choice reappears on widening", () => {
    const closed = { railOpen: false, pagesOpen: false };
    expect(shellLayout(1600, closed)).toMatchObject({ railKind: "strip", pagesKind: "collapsed" });
    expect(shellLayout(1600, closed).widths).toMatchObject({ rail: STRIP_W, pages: EDGE_W });
    // narrowing then widening with the default prefs: the function is pure, so the same prefs give
    // back the same docked layout — an auto-collapse has nothing to overwrite.
    const prefs = { ...DEFAULT_PREFS };
    shellLayout(700, prefs);
    expect(prefs).toEqual(DEFAULT_PREFS);
    expect(shellLayout(1600, prefs)).toMatchObject({ railKind: "docked", pagesKind: "docked" });
  });

  it("acceptance row: drag pages to 520, narrow to compact, widen — the panel returns at 520", () => {
    const stored = { wide: { pages: 520 } };
    expect(shellLayout(1600, DEFAULT_PREFS, stored).widths.pages).toBe(520);
    expect(shellLayout(1024, DEFAULT_PREFS, stored).widths.pages).toBe(MODES.compact.pages!.def);
    expect(shellLayout(1600, DEFAULT_PREFS, stored).widths.pages).toBe(520);
  });

  it("a stored width is clamped on read, never discarded", () => {
    const stored = { wide: { pages: 5000, rail: 10 } };
    const L = shellLayout(1600, DEFAULT_PREFS, stored);
    expect(L.widths.pages).toBe(L.bounds.pages!.max);
    expect(L.widths.rail).toBe(MODES.wide.rail!.min);
    // and still there for a wider window
    expect(shellLayout(2560, DEFAULT_PREFS, stored).widths.pages).toBe(Math.round(2560 * 0.6));
  });

  it("junk in storage is ignored, not trusted", () => {
    const stored = { wide: { pages: Number.NaN, rail: -4 } } as never;
    const L = shellLayout(1600, DEFAULT_PREFS, stored);
    expect(L.widths.pages).toBe(480);
    expect(L.widths.rail).toBe(240);
  });
});

describe("splitter bounds", () => {
  it("both panes resize in docked modes, within the table's ranges", () => {
    const L = shellLayout(1600);
    expect(L.bounds.rail).toMatchObject({ min: 200, max: 320 });
    expect(L.bounds.pages).toMatchObject({ min: 320 });
    const D = shellLayout(1280);
    expect(D.bounds.rail).toMatchObject({ min: 200, max: 280 });
  });

  it("no bounds where a pane does not dock — there is nothing to drag", () => {
    expect(shellLayout(820).bounds).toEqual({});
    expect(shellLayout(1024).bounds.rail).toBeUndefined();
  });

  it("max is never below min, across the sweep", () => {
    for (const vw of SWEEP) for (const prefs of ALL_PREFS) {
      const b = shellLayout(vw, prefs).bounds;
      if (b.rail) expect(b.rail.max).toBeGreaterThanOrEqual(b.rail.min);
      if (b.pages) expect(b.pages.max).toBeGreaterThanOrEqual(b.pages.min);
    }
  });
});

describe("phones (founder report 2026-10-10, Android Chrome at 412px)", () => {
  // portrait 360 / 390 / 412 and their landscape widths: below 720 the conversation is the whole
  // window and the side panes are overlays that take no column at all.
  for (const vw of [360, 375, 390, 412, 430]) {
    it(`${vw}px portrait: the conversation is the full width; rail and pages take no column`, () => {
      for (const prefs of ALL_PREFS) {
        const L = shellLayout(vw, prefs);
        expect(L.mode).toBe("single");
        expect(L.columns).toBe(`0px minmax(0, 1fr) 0px`);
        expect(L.widths.chat).toBe(vw);
        expect(L.railKind).toBe("drawer");
        expect(L.pagesKind).toBe("fullscreen");
      }
    });
  }
  for (const vw of [740, 844, 915]) {
    it(`${vw}px landscape: narrow mode, the strip and the conversation fill the window`, () => {
      const L = shellLayout(vw);
      expect(L.widths.rail + L.widths.chat).toBe(vw);
      expect(L.widths.pages).toBe(0);
    });
  }
});
