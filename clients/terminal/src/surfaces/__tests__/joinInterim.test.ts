/** F40 — INTERIM TEXTS ARE PARAGRAPHS, NOT ONE RUN-ON. Founder ruling 2026-09-02.
 *
 *  He read this on screen: `"created here.I'll set up a shared workspace…"` — two separate
 *  narrations concatenated into a sentence that reads as one and parses as neither.
 *
 *  `message-delta` carries a TOKEN when the worker streams partials and a WHOLE assistant text block
 *  when it does not, so the fix cannot be "put a break between deltas": that would shatter every
 *  token-streamed sentence. The observable boundary is a TOOL CALL — an assistant message ends when
 *  the model reaches for a tool — so a tool call arms the break and the next delta spends it.
 *
 *  Which also answers the question left open about his first setup turn: the three narration lines
 *  were ONE agent turn. The client keeps a single agent turn per dispatch and appends every
 *  `message-delta` into its text; there is no path by which interim text becomes separate bubbles.
 *  What he saw was one bubble whose text had been concatenated without separators. */
import { describe, expect, it } from "vitest";
import { joinInterim } from "../chatStream";

describe("joinInterim", () => {
  it("token deltas inside one block are concatenated exactly as before", () => {
    let out = "";
    for (const tok of ["I'll ", "set ", "up ", "a ", "workspace"]) out = joinInterim(out, tok, false);
    expect(out).toBe("I'll set up a workspace");
  });

  it("THE DEFECT: a second narration after a tool call is its own paragraph", () => {
    expect(joinInterim("created here.", "I'll set up a shared workspace…", true))
      .toBe("created here.\n\nI'll set up a shared workspace…");
  });

  it("the break is spent once — the tokens after it keep flowing into the same paragraph", () => {
    let out = joinInterim("created here.", "I'll set up", true);
    out = joinInterim(out, " a shared workspace", false);
    expect(out).toBe("created here.\n\nI'll set up a shared workspace");
  });

  it("does not open a turn with a blank line", () => {
    expect(joinInterim("", "Reading the workspace…", true)).toBe("Reading the workspace…");
    expect(joinInterim("   ", "Reading the workspace…", true)).toBe("   Reading the workspace…");
  });

  it("does not double a break the text already ends with", () => {
    expect(joinInterim("a list:\n", "- one", true)).toBe("a list:\n- one");
    expect(joinInterim("done.\n\n", "next", true)).toBe("done.\n\nnext");
  });
});
