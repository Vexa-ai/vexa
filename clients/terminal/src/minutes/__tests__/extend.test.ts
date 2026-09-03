/** "EXTEND" — the decisions, without a DOM (PRD decision 32, F63).
 *
 *  What has a plausible wrong answer here: an intent built from a display string instead of the
 *  resolved slot; a bubble that quietly grows into the prompt; a `selection_range` that points
 *  somewhere with the authority of a number; a landing that fires twice.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { normalizeIntent, SELECTION_MAX, type ChatIntent, type PageIntent } from "../../surfaces/chatIntent";
import {
  PREVIEW_MAX, clearPending, compactLabel, fallbackText, landPending, pendingLanding, postIntent, sourceRange,
} from "../extend";
import { VIEW_NAVIGATE_EVENT } from "../roomView";

// these suites are about PAGE intents (extend/create); narrowing here is the assertion, not a cast
const asks: { prompt?: string; display?: string; intent?: PageIntent }[] = [];
const views: unknown[] = [];
const onAsk = (e: Event) => asks.push((e as CustomEvent).detail);
const onView = (e: Event) => views.push((e as CustomEvent).detail);

beforeEach(() => {
  asks.length = 0; views.length = 0; clearPending();
  window.addEventListener(ASK_CHAT_EVENT, onAsk);
  window.addEventListener(VIEW_NAVIGATE_EVENT, onView);
});
afterEach(() => {
  window.removeEventListener(ASK_CHAT_EVENT, onAsk);
  window.removeEventListener(VIEW_NAVIGATE_EVENT, onView);
});

describe("what an intent may say (F63 — never a guessed path)", () => {
  it("carries the workspace and the path it was given", () => {
    expect(normalizeIntent({ kind: "extend", workspace: "acme-kg", path: "kg/plan.md" }))
      .toEqual({ kind: "extend", workspace: "acme-kg", path: "kg/plan.md" });
  });

  it("an absent workspace is the reader's own desk, and stays ABSENT — never an empty string", () => {
    const i = normalizeIntent({ kind: "extend", path: "README.md" })!;
    expect(i.workspace).toBeUndefined();
    expect("workspace" in i).toBe(false);
    expect(normalizeIntent({ kind: "extend", workspace: "   ", path: "README.md" })!.workspace).toBeUndefined();
  });

  it("refuses a path that is missing, empty, or walks out of its mount", () => {
    expect(normalizeIntent({ kind: "extend", path: "" })).toBeNull();
    expect(normalizeIntent({ kind: "extend", path: "   " })).toBeNull();
    expect(normalizeIntent({ kind: "extend", path: undefined })).toBeNull();
    expect(normalizeIntent({ kind: "extend", path: "../../etc/passwd" })).toBeNull();
  });

  it("refuses a kind it does not know", () => {
    expect(normalizeIntent({ kind: "explode" as "extend", path: "a.md" })).toBeNull();
  });

  it("trims the selection and caps it at 2000 characters", () => {
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection: "  hi  " })!.selection).toBe("hi");
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection: "   " })!.selection).toBeUndefined();
    const long = "x".repeat(SELECTION_MAX + 500);
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection: long })!.selection).toHaveLength(SELECTION_MAX);
  });

  it("drops a range that has no selection, or that does not measure its own selection", () => {
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection_range: { start: 0, end: 5 } })!.selection_range).toBeUndefined();
    // "hello" is 5 long; a range claiming 9 is pointing somewhere else with a number's authority
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection: "hello", selection_range: { start: 3, end: 12 } })!.selection_range).toBeUndefined();
    expect(normalizeIntent({ kind: "extend", path: "a.md", selection: "hello", selection_range: { start: 3, end: 8 } })!.selection_range).toEqual({ start: 3, end: 8 });
  });
});

describe("where a selection sits in the SOURCE", () => {
  const body = "# Plan\n\nThe pilot ships in March.\n\nThe pilot ships in March again.\n";

  it("locates an unambiguous selection", () => {
    expect(sourceRange(body, "in March again")).toEqual({ start: 51, end: 65 });
  });

  it("returns nothing when the text occurs twice — ambiguous is not near-miss", () => {
    expect(sourceRange(body, "The pilot ships")).toBeNull();
  });

  it("returns nothing when the rendered selection is not in the source at all", () => {
    expect(sourceRange(body, "a heading rendered without its hash")).toBeNull();
    expect(sourceRange(null, "anything")).toBeNull();
  });
});

describe("the bubble is the compact form, never the prompt", () => {
  const page: ChatIntent = { kind: "extend", path: "kg/entities/company/helm.md" };

  it("names the verb and the page", () => {
    expect(compactLabel(page)).toBe("Extend: kg/entities/company/helm.md");
    expect(compactLabel({ kind: "create", path: "kg/plan.md" })).toBe("Create: kg/plan.md");
  });

  it("quotes a short selection, and shortens a long one", () => {
    expect(compactLabel({ ...page, selection: "the pilot ships in March" }))
      .toBe('Extend: kg/entities/company/helm.md — “the pilot ships in March”');
    const long = compactLabel({ ...page, selection: "word ".repeat(60) });
    expect(long.length).toBeLessThan(page.path.length + PREVIEW_MAX + 20);
    expect(long.endsWith("…”")).toBe(true);
  });

  it("flattens a selection dragged across paragraphs — a label is one line", () => {
    expect(compactLabel({ ...page, selection: "first line\n\nsecond line" }))
      .toBe('Extend: kg/entities/company/helm.md — “first line second line”');
  });

  it("the PROMPT keeps the whole selection — it is what the agent reads", () => {
    const selection = "word ".repeat(60).trim();
    expect(fallbackText({ ...page, selection })).toContain(selection);
    expect(fallbackText(page)).toBe("Extend: kg/entities/company/helm.md");
  });
});

describe("posting into the open chat", () => {
  it("sends the intent AND a plain-text fallback, with the compact form as the display", () => {
    const sent = postIntent({ kind: "extend", workspace: "acme-kg", path: "kg/plan.md" });
    expect(sent).toEqual({ kind: "extend", workspace: "acme-kg", path: "kg/plan.md" });
    expect(asks).toHaveLength(1);
    expect(asks[0].intent).toEqual(sent);
    expect(asks[0].display).toBe("Extend: kg/plan.md");
    expect(asks[0].prompt).toBe("Extend: kg/plan.md");
  });

  it("a selection travels on the intent and inside the fallback sentence", () => {
    postIntent({ kind: "extend", path: "a.md", selection: "  the pilot ships  " });
    expect(asks[0].intent?.selection).toBe("the pilot ships");
    expect(asks[0].prompt).toBe("Extend: a.md — 'the pilot ships'");
    expect(asks[0].display).toBe('Extend: a.md — “the pilot ships”');
  });

  it("an intent it cannot honour is not sent at all — no bubble, no turn", () => {
    expect(postIntent({ kind: "extend", path: "" })).toBeNull();
    expect(asks).toHaveLength(0);
    expect(pendingLanding()).toBeNull();
  });
});

describe("the landing (decision 32.3)", () => {
  it("the posted page becomes the view — once", () => {
    postIntent({ kind: "create", workspace: "acme-kg", path: "kg/new.md" });
    expect(pendingLanding()).toEqual({ workspace: "acme-kg", path: "kg/new.md" });

    expect(landPending()).toBe(true);
    expect(views).toEqual([{ workspace: "acme-kg", path: "kg/new.md", label: "new" }]);

    expect(landPending()).toBe(false);      // a second commit does not re-navigate
    expect(views).toHaveLength(1);
  });

  it("nothing pending lands nothing", () => {
    expect(landPending()).toBe(false);
    expect(views).toEqual([]);
  });

  it("a second press replaces the first — the newest is what the reader asked for", () => {
    postIntent({ kind: "extend", path: "one.md" });
    postIntent({ kind: "extend", path: "two.md" });
    landPending();
    expect(views).toEqual([{ workspace: undefined, path: "two.md", label: "two" }]);
  });
});
