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

describe("G6 — motion respects reduced motion", () => {
  // Every animation or transition in a stylesheet is timed by a duration TOKEN (which collapses to
  // 0 under prefers-reduced-motion in tokens.css) or sits inside a `no-preference` block.
  const ALLOW = new Set<string>([]);
  it("no literal-duration animation or transition outside a reduced-motion guard", () => {
    const bad: string[] = [];
    for (const s of SOURCES.filter((x) => x.rel.endsWith(".css") && !ALLOW.has(x.rel))) {
      // drop the guarded blocks, then look for literal durations in what is left
      const unguarded = s.text.replace(/@media\s*\(prefers-reduced-motion:\s*no-preference\)\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
      for (const m of unguarded.matchAll(/(animation|transition)\s*:[^;]*;/g)) {
        if (/\b\d+(\.\d+)?m?s\b/.test(m[0]) && !/var\(--dur-/.test(m[0])) bad.push(`${s.rel}: ${m[0].slice(0, 80)}`);
      }
    }
    expect(bad).toEqual([]);
  });
});

describe("G8 — no secret in an attribute", () => {
  it("nothing named token / secret / apiKey / password flows into title, aria-label or data-*", () => {
    const bad: string[] = [];
    for (const s of SOURCES.filter((x) => x.rel.endsWith(".tsx"))) {
      for (const m of s.text.matchAll(/\b(title|aria-label|data-[\w-]+)=\{([^}]*)\}/g)) {
        // a value, not a NAME for one: `secret_label`, `tokenName`, `tokenCount` are labels and counts
        const names = m[2].replace(/"[^"]*"|'[^']*'|`[^`]*`/g, "").replace(/\b\w*(token|secret|password)_?(label|name|count|scope|kind|s)\w*\b/gi, "");
        if (/\b(token|secret|apiKey|api_key|password)\b/i.test(names)) bad.push(`${s.rel}: ${m[0].slice(0, 90)}`);
      }
    }
    expect(bad).toEqual([]);
  });
});

describe("G9 — external links go through ExternalLink", () => {
  const ALLOW = new Set(["ui-kit/primitives/Links.tsx", "ui-kit/primitives/Truncate.tsx"]);
  it('no <a target="_blank"> outside the link primitives', () => {
    const bad = SOURCES.filter((s) => !ALLOW.has(s.rel) && /<a\b[^>]*target=["{]?["']?_blank/.test(s.text)).map((s) => s.rel);
    expect(bad).toEqual([]);
  });
});

describe("G10 — no raw HTML injection", () => {
  // the pre-paint theme script, and the sanitised Mermaid SVG; the canvas manifest and validator
  // only NAME the prop, to refuse it in agent-authored canvases
  const ALLOW = new Set(["app/layout.tsx", "ui-kit/docDiagrams.tsx", "canvas/manifest.ts", "canvas/validator.ts"]);
  it("dangerouslySetInnerHTML only in the allowlisted files", () => {
    const bad = SOURCES.filter((s) => !ALLOW.has(s.rel) && /dangerouslySetInnerHTML/.test(s.text)).map((s) => s.rel);
    expect(bad).toEqual([]);
  });
});
