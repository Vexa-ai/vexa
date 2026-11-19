/** Founder 2026-10-16, in a room: a reply the room's CHAT woke was labelled "From the meeting"; a
 *  step leaked a file name; and "hi" rendered one letter per line. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { foldSignalWakes, wakeLabel } from "../signalWake";
import { toolOp } from "../chat";

describe("what woke a reply is named by its source", () => {
  it("a room chat's wake says From the chat; a call's says From the call", () => {
    const chat = foldSignalWakes([{ role: "user", raw: "[vexa-flow:room_signal:wake] lines…" }, { role: "agent", text: "Noted." }]);
    expect(wakeLabel(chat[0])).toBe("From the chat");
    const call = foldSignalWakes([{ role: "user", raw: "[vexa-flow:meeting_signal:wake] lines…" }, { role: "agent", text: "Noted." }]);
    expect(wakeLabel(call[0])).toBe("From the call");
  });
});

describe("a step never shows a file name", () => {
  it("names the page by its title, never its .md file", () => {
    expect(toolOp("Read", { file_path: "/w/ait/ait-lisbon-next-meetup-nov-2026.md" }).label).toBe("Reading · Ait lisbon next meetup nov 2026");
    expect(toolOp("Read", { file_path: "/w/ait/README.md" }).label).toBe("Reading · README");
  });
});

describe("a short message stays on one line", () => {
  it("the bubble fits its content with a minimum width, in a body as wide as the turn, and never breaks all", () => {
    const css = readFileSync(join(__dirname, "../mentionUI.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = (sel: string) => [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter((m) => m[1].split(",").map((x) => x.trim()).includes(sel)).map((m) => m[2]).join(";");
    const bubble = rule(".vx-bubble");
    expect(bubble).toMatch(/width\s*:\s*fit-content/);
    expect(bubble).toMatch(/min-width\s*:\s*2\.5em/);
    expect(bubble).not.toMatch(/word-break\s*:\s*break-all|overflow-wrap\s*:\s*anywhere/);
    expect(rule(".vx-turn-mine > .vx-turn-other-body")).toMatch(/align-self\s*:\s*stretch/);
  });
});
