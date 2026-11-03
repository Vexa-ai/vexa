/** Every feature switch the terminal reads is registered by the server (#1783, R0 hygiene).
 *  The terminal fails closed on a name the server does not answer, so a name read here but missing
 *  from agent-api's registry (`core/agent/control_plane/features.py` FEATURES) would be off for ever.
 *  This reads both sides from the repo: SWITCHABLE, every literal passed to useFeature / featureOn /
 *  useFeatureAnswer under src/, and the registry's keys. */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { SWITCHABLE } from "../features";

const TERMINAL = resolve(__dirname, "../../..");
const REGISTRY = resolve(TERMINAL, "../../core/agent/control_plane/features.py");

function registered(): Set<string> {
  const src = readFileSync(REGISTRY, "utf8");
  const start = src.indexOf("FEATURES: dict[str, dict] = {");
  expect(start).toBeGreaterThan(-1);
  const body = src.slice(start, src.indexOf("\n}\n", start));
  return new Set([...body.matchAll(/^ {4}"([a-z0-9_]+)":\s*\{/gm)].map((m) => m[1]));
}

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "__tests__") continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(ts|tsx|mjs)$/.test(name) && !/\.test\./.test(name)) out.push(p);
  }
  return out;
}

function namesRead(): Set<string> {
  const out = new Set<string>();
  for (const f of sources(join(TERMINAL, "src"))) {
    for (const m of readFileSync(f, "utf8").matchAll(/\b(?:useFeature|featureOn|useFeatureAnswer)\(\s*["']([a-z0-9_]+)["']/g)) out.add(m[1]);
  }
  return out;
}

describe("feature registry", () => {
  it("registers every SWITCHABLE name server-side", () => {
    const reg = registered();
    expect(reg.size).toBeGreaterThan(5);
    expect(SWITCHABLE.filter((n) => !reg.has(n))).toEqual([]);
  });

  it("registers every name the terminal reads, and lists it in SWITCHABLE", () => {
    const reg = registered();
    const read = [...namesRead()];
    expect(read.length).toBeGreaterThan(5);
    expect(read.filter((n) => !reg.has(n))).toEqual([]);
    expect(read.filter((n) => !(SWITCHABLE as readonly string[]).includes(n))).toEqual([]);
  });
});
