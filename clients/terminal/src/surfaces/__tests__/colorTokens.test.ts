/** Palette guard (design-spec meeting-lifecycle-v2, W1): the semantic token set is the ONLY
 *  color source. Two regressions this catches:
 *    1. `var(--live` — the old red "live" token is deleted (live is GREEN; red = danger only).
 *       A stale reference renders unstyled, so we fail the build instead.
 *    2. hardcoded hex colors in component sources — every color goes through globals.css
 *       tokens so both themes and future palette changes stay one-file edits.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..");
const SCAN_DIRS = ["surfaces", "canvas", "workbench", "ui-kit", "app"];
// Non-color or deliberate exceptions:
//  - routines.tsx switch knob: a white knob is correct on both themes' green track.
//  - AuthGate/App boxShadow rgba + icon assets are shadows/artwork, not palette colors.
const HEX_ALLOWLIST = new Set(["surfaces/routines.tsx"]);
const HEX_RE = /(?:color|background|border(?:Color|Bottom|Top|Left|Right)?)\s*:\s*[^,;}]*#[0-9a-fA-F]{3,8}\b/;

function* sourceFiles(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "__tests__" || name === "node_modules") continue;
      yield* sourceFiles(p);
    } else if (/\.(tsx|ts|css)$/.test(name) && !name.endsWith(".test.ts") && !name.endsWith(".test.tsx")) {
      yield p;
    }
  }
}

describe("palette guard", () => {
  it("no source references the deleted --live token", () => {
    const offenders: string[] = [];
    for (const dir of SCAN_DIRS) {
      for (const f of sourceFiles(join(SRC, dir))) {
        if (readFileSync(f, "utf8").includes("var(--live")) offenders.push(f.slice(SRC.length + 1));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("component styles use tokens, not hardcoded hex colors", () => {
    const offenders: string[] = [];
    for (const dir of SCAN_DIRS) {
      for (const f of sourceFiles(join(SRC, dir))) {
        const rel = f.slice(SRC.length + 1);
        if (rel === "app/globals.css" || HEX_ALLOWLIST.has(rel)) continue;
        const src = readFileSync(f, "utf8");
        for (const line of src.split("\n")) {
          if (HEX_RE.test(line)) { offenders.push(`${rel}: ${line.trim().slice(0, 100)}`); break; }
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
