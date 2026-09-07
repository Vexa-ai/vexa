/** IN A MEETING CHAT THE TRANSCRIPT IS A TAB THAT CANNOT BE CLOSED (Vexa-ai/vexa#1600).
 *
 *  Founder, 2026-09-06, shown the "Open transcript" chip #1586 had put above the composer:
 *
 *      *"just keep a tab that can't be closed instead"*
 *
 *  #1586 was a way BACK: he had pressed `×` on the transcript, the tab was gone, and the chip
 *  existed so he could get it again. This ruling removes the thing he was recovering from. The
 *  meeting's own pages — the transcript, and the meeting's page once it has one — belong to the
 *  MEETING rather than to the reader's tab habits, so they carry no close control and every close
 *  path refuses them; the chips go with the problem they solved.
 *
 *  ── WHAT CAN FAIL QUIETLY HERE, which is what each block below is for ──────────────────────────
 *
 *  · A missing `×` is not a refusal. The button is one close path; `forgetHistory` is another, an
 *    unpin of a tab that is not in front is a third (it DROPS the entry), and the preview cap is a
 *    fourth. A rule enforced only in the render is a rule any later caller walks around.
 *  · An ordinary pin must keep its `×`. "Nothing closes" is the easy over-shoot, and it takes away
 *    a decision the reader did make.
 *  · The flag is a property of the PAGE, so a strip stored before this rule carries none — and the
 *    stored strips are exactly the meeting chats somebody has used, the founder's own included.
 *    A fix that only reaches freshly-composed rooms reaches nobody who has the problem.
 *  · The chips are DELETED, not made unreachable — the standard `noDefaults.test.ts` sets for a
 *    ruling of this shape: a string in a branch nobody currently enters is waiting for the next
 *    refactor, and "I explain this as stale code."
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render } from "@testing-library/react";

import {
  artifactKey, forgetHistory, PREVIEW_CAP, stripForRecord, togglePinned, touchHistory, withHome,
  CHATS_KEY, loadChats, type Artifact, type Chat,
} from "../chats";
import { meetingPages, pagesForPhase, withMeetingPages } from "../roomView";
import { PagesPanel } from "../PagesPanel";
import type { Page } from "../types";

const NOTE = "kg/entities/meeting/2026-09-06-1200-google-meet.md";
const paths = (l: { path: string }[]) => l.map((a) => a.path);

// ── what is permanent, and what is not ───────────────────────────────────────────────────────────

describe("the MEETING's pages are permanent; the room's are not", () => {
  it("stamps the transcript and the meeting's own page", () => {
    expect(meetingPages("live", "118", NOTE)).toEqual([
      { kind: "meeting", path: "118", label: "Transcript", permanent: true },
      { path: NOTE, label: "Brief", permanent: true },
    ]);
  });

  it("…in every phase, under whichever name the page is read by", () => {
    expect(meetingPages("post", "118", NOTE).every((pg) => pg.permanent)).toBe(true);
    // prep has no transcript at all, and the one page it does have is still the meeting's
    expect(meetingPages("prep", "118", NOTE)).toEqual([{ path: NOTE, label: "Brief", permanent: true }]);
  });

  it("a `?mock=1` fixture's canned transcript is the meeting's page too", () => {
    // it is a markdown file rather than the canvas, and that is a fact about where the fixture's
    // words come from — not about whether the reader may close the room's transcript
    expect(meetingPages("post", null, null, "mock-post")[0])
      .toEqual({ path: "kg/entities/meeting/mock-post.transcript.md", label: "Transcript", permanent: true });
  });

  it("the PERSONAL page is the room's, not the meeting's — it stays closable", () => {
    const room = pagesForPhase("live", "abc", "118", NOTE);
    expect(room.map((pg) => !!pg.permanent)).toEqual([true, true, false]);
    expect(room[2].label).toBe("Personal page");
  });

  it("and an ordinary document is never permanent, however it reached the strip", () => {
    expect(withHome([], []).every((a) => !a.permanent)).toBe(true);
    expect(touchHistory([], { path: "drafts/plan.md", label: "plan" }, 1)[0].permanent).toBeUndefined();
  });
});

// ── every close path refuses them ────────────────────────────────────────────────────────────────

describe("every close path refuses a permanent tab", () => {
  const transcript: Artifact = { kind: "meeting", path: "118", label: "Transcript", pinned: true, permanent: true };
  const note: Artifact = { path: NOTE, label: "Minutes", pinned: true, permanent: true };
  const kept: Artifact = { path: "_global/PRINCIPLES.md", slug: "_global", label: "PRINCIPLES", pinned: true };
  const strip = [{ path: "README.md", label: "Desk", desk: true } as Artifact, transcript, note, kept];

  it("`×` — the code path behind the button — leaves them where they are", () => {
    expect(paths(forgetHistory(strip, artifactKey(transcript)))).toEqual(paths(strip));
    expect(paths(forgetHistory(strip, artifactKey(note)))).toEqual(paths(strip));
  });

  it("…while an ordinary pin still closes, because the reader chose to keep that one", () => {
    expect(paths(forgetHistory(strip, artifactKey(kept)))).toEqual(["README.md", "118", NOTE]);
  });

  it("UNPINNING is a close in disguise, and it is refused too", () => {
    // a pin that is not the page in front is DROPPED when it is unpinned (`togglePinned`), so this
    // is the same deletion arriving through the other control on the tab
    expect(togglePinned(strip, artifactKey(transcript), false, 9)).toBe(strip);
    expect(togglePinned(strip, artifactKey(transcript), true, 9)).toBe(strip);
    expect(togglePinned(strip, artifactKey(note), false, 9)).toBe(strip);
  });

  it("the preview cap never evicts one, even if something hands it over unpinned", () => {
    let l: Artifact[] = [{ kind: "meeting", path: "118", label: "Transcript", permanent: true }];
    for (let i = 1; i <= 5; i++) l = touchHistory(l, { path: `f${i}.md`, label: `f${i}` }, i);
    expect(l.some((a) => a.kind === "meeting")).toBe(true);
    expect(l.filter((a) => !a.pinned && !a.desk && !a.permanent)).toHaveLength(PREVIEW_CAP);
  });

  it("navigating to one keeps it permanent — the flag survives the round trip", () => {
    const l = touchHistory([transcript], { kind: "meeting", path: "118", label: "Transcript" }, 7);
    expect(l[0].permanent).toBe(true);
    expect(l[0].pinned).toBe(true);
  });
});

// ── the panel: no control to press ───────────────────────────────────────────────────────────────

describe("the pages panel renders no close control on them", () => {
  const STRIP: Page[] = [
    { path: "README.md", label: "Desk", desk: true },
    { kind: "meeting", path: "118", label: "Transcript", pinned: true, permanent: true },
    { path: NOTE, label: "Minutes", pinned: true, permanent: true },
    { path: "_global/PRINCIPLES.md", slug: "_global", label: "PRINCIPLES", pinned: true },
    { path: "drafts/plan.md", label: "plan", at: 3 },
  ];
  const onClose = vi.fn();
  const onTogglePin = vi.fn();
  const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
    render(<PagesPanel pages={STRIP} docPath="drafts/plan.md" onOpen={() => {}}
      onClose={onClose} onTogglePin={onTogglePin} body="# plan" {...over} />);

  beforeEach(() => { onClose.mockClear(); onTogglePin.mockClear(); });
  afterEach(() => cleanup());

  it("gives a `×` to every tab EXCEPT the meeting's own", () => {
    const { container } = panel();
    const closes = [...container.querySelectorAll("[data-tab-close]")].map((b) => b.getAttribute("aria-label"));
    expect(closes).not.toContain("Close Transcript");
    expect(closes).not.toContain("Close Minutes");
    // …and the tabs that are the reader's still have theirs — "nothing closes" is the over-shoot
    expect(closes).toContain("Close PRINCIPLES");
    expect(closes).toContain("Close plan");
  });

  it("gives them no pin either — unpinning one would be the close it must not have", () => {
    const { container } = panel();
    expect([...container.querySelectorAll("[data-tab-pin]")].map((b) => b.getAttribute("aria-label")))
      .toEqual(["Unpin PRINCIPLES", "Keep plan as a tab"]);
  });

  it("still renders them as tabs, kept and legible — invisible is not the same as unclosable", () => {
    const { container } = panel();
    const tabs = [...container.querySelectorAll("[data-tab]")];
    expect(tabs.map((t) => t.textContent)).toEqual(["Desk", "Transcript", "Minutes", "PRINCIPLES", "plan"]);
    expect(tabs.map((t) => t.hasAttribute("data-kept"))).toEqual([true, true, true, true, false]);
  });

  it("an ordinary pinned tab's `×` still closes it, and names the page it is about", () => {
    const { container } = panel();
    fireEvent.click([...container.querySelectorAll("[data-tab-close]")]
      .find((b) => b.getAttribute("aria-label") === "Close PRINCIPLES")!);
    expect(onClose).toHaveBeenCalledWith(STRIP[3]);
  });
});

// ── the strips that already exist ────────────────────────────────────────────────────────────────

describe("a strip stored before the ruling is told which tabs are the meeting's", () => {
  const desk: Artifact = { path: "README.md", label: "Desk", desk: true };
  /** his own chat, as it sits on disk today: the meeting's tabs, pinned, and no `permanent` */
  const stored: Artifact[] = [
    desk,
    { kind: "meeting", path: "118", label: "Transcript", pinned: true, at: 4 },
    { path: NOTE, label: "Minutes", pinned: true, at: 5 },
    { path: "drafts/plan.md", label: "plan", at: 6 },
  ];

  it("stamps them in place, and touches nothing else", () => {
    const out = withMeetingPages(stored, meetingPages("post", "118", NOTE));
    const byPath = Object.fromEntries(out.map((a) => [a.path, a]));
    expect(byPath["118"].permanent).toBe(true);
    expect(byPath[NOTE].permanent).toBe(true);
    // the page the reader was on is theirs, and still closable
    expect(byPath["drafts/plan.md"]).toEqual({ path: "drafts/plan.md", label: "plan", at: 6 });
    expect(byPath["README.md"].permanent).toBeUndefined();
  });

  it("gives back a transcript that was closed under the OLD rule", () => {
    // there is nothing to preserve here: closing it is not a decision this product offers any more,
    // and this is the state the founder was in when he asked for the tab (#1597 → #1600)
    const closed = [desk, { path: "drafts/plan.md", label: "plan", at: 6 } as Artifact];
    const out = withMeetingPages(closed, meetingPages("post", "118", NOTE));
    expect(paths(out)).toEqual(["README.md", "118", NOTE, "drafts/plan.md"]);
    expect(out.filter((a) => a.permanent)).toHaveLength(2);
  });

  it("is idempotent — opening the chat twice is opening it once", () => {
    const room = meetingPages("post", "118", NOTE);
    const once = withMeetingPages(stored, room);
    expect(withMeetingPages(once, room)).toEqual(once);
  });
});

describe("and the flag survives the way back to disk", () => {
  beforeEach(() => localStorage.clear());

  it("`stripForRecord` copies it — the writer persists what the strip IS", () => {
    const pg: Artifact = { kind: "meeting", path: "118", label: "Transcript", pinned: true, permanent: true, at: 2 };
    expect(stripForRecord([pg])[0].permanent).toBe(true);
  });

  it("a reload reads it back, so the tab is still unclosable in the next session", () => {
    const c = {
      id: "c1", label: "c1", meeting: "118", workspaces: ["personal"], touched: true,
      createdAt: 1, lastActivityAt: 1,
      artifacts: [{ kind: "meeting", path: "118", label: "Transcript", pinned: true, permanent: true, at: 2 }],
    } as unknown as Chat;
    localStorage.setItem(CHATS_KEY, JSON.stringify([c]));
    expect(loadChats().find((x) => x.id === "c1")?.artifacts[0].permanent).toBe(true);
  });
});

// ── the chips are gone, not merely unreachable ───────────────────────────────────────────────────

/** Every `.ts`/`.tsx` under `src/`, tests excluded — the same walk `noDefaults.test.ts` makes, and
 *  for the same reason: a deleted string may legitimately be QUOTED in a test (this file quotes
 *  them all) while being absent from everything that renders. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "__tests__" || name === ".next") continue;
      sourceFiles(p, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

describe("the open chips are deleted from the source", () => {
  const SRC = join(process.cwd(), "src");
  // comments are where a deletion is EXPLAINED, and an explanation has to be able to name what it
  // deleted — so, as in `noDefaults.test.ts`, the search is over code only
  const CODE = sourceFiles(SRC).map((f) => ({
    f: f.slice(SRC.length + 1),
    src: readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1"),
  }));

  const GONE: [string, string][] = [
    ["Open transcript", "the chip the founder was shown when he ruled"],
    ["openChips", "the pure rule behind the row"],
    ["OpenChips", "the row itself"],
    ["data-open-chip", "…and the handle a test drove it by"],
  ];

  for (const [needle, what] of GONE) {
    it(`${needle} — ${what}`, () => {
      expect(CODE.filter(({ src }) => src.includes(needle)).map(({ f }) => f)).toEqual([]);
    });
  }
});
