/** Design guards that already hold across the whole terminal (guidelines §8):
 *    G12  no window.confirm / window.alert — a destructive act asks in a ConfirmDialog that names
 *         the act; window.prompt is allowlisted where a replacement has not landed yet;
 *    G4   no overflow-wrap:anywhere / word-break:break-all outside Code and SecretReveal (and the
 *         places listed with their reason) — text never breaks mid-word. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..");
function* files(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== "__tests__" && name !== "node_modules") yield* files(p); }
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) yield p;
  }
}
const SOURCES = [...files(SRC)].map((f) => ({ rel: f.slice(SRC.length + 1), text: readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "") }));

describe("G12 — no browser dialogs", () => {
  const PROMPT_ALLOW = new Set(["surfaces/meeting.tsx"]);   // the schedule-time prompt: a DateTimePicker replaces it in Phase 3
  it("no window.confirm or window.alert", () => {
    const bad = SOURCES.filter((s) => /window\.(confirm|alert)\s*\(/.test(s.text)).map((s) => s.rel);
    expect(bad).toEqual([]);
  });
  it("window.prompt only where allowlisted", () => {
    const bad = SOURCES.filter((s) => /window\.prompt\s*\(/.test(s.text) && !PROMPT_ALLOW.has(s.rel)).map((s) => s.rel);
    expect(bad).toEqual([]);
  });
});

describe("G4 — no mid-word breaks", () => {
  // Places allowed to break anywhere, each with its reason.
  const ALLOW = new Set([
    "ui-kit/primitives/controls.css",      // Code and SecretReveal: a hash or key has no word boundaries
    "minutes/DestinationHost.tsx",          // a single host name shown large, which must fit its box
    "minutes/AttachRepo.tsx",               // a verbatim error and a deploy key: code, not prose
    "minutes/OAuthConnectionForm.tsx",      // a redirect URL to copy exactly: code, not prose
    "surfaces/ServiceDenialPanel.tsx",      // the verbatim fault for support: code, not prose
    "minutes/MeetingPageHeader.tsx",        // reworked on Vexa-ai/vexa#1798; moves onto KeyValue with it
    "canvas/kit.tsx",                       // the agent canvas kit; re-pointed at primitives in Phase 2f
  ]);
  it("overflow-wrap:anywhere and word-break:break-all appear only in the allowlisted places", () => {
    const bad = SOURCES.filter((s) => !ALLOW.has(s.rel) && /overflow-?[wW]rap\s*:\s*["']?anywhere|word-?[bB]reak\s*:\s*["']?break-all/.test(s.text))
      .map((s) => s.rel);
    expect(bad).toEqual([]);
  });
});
