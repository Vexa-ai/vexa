/** The reload bar's decision logic (PRD decision 39).
 *
 *  These are the tests that replace a human ritual. Until 2026-09-02 the founder was asked to go
 *  "out" while containers were recreated and "in" when they were back — a person doing, by hand,
 *  the job of noticing that the page in front of him was older than the deployment behind it. The
 *  rules below are that job, written down; the interesting half of them is the NON-events, because
 *  a bar that fires on every swap-in-progress is one nobody reads by the time it is true.
 */
import { describe, expect, it, vi } from "vitest";
import {
  baselineOf, foldBaseline, readVersion, reloadOffered,
  type Baseline, type VersionReport,
} from "../versionWatch";

const report = (over: Partial<VersionReport> = {}): VersionReport => ({
  terminal: { build: "line-aaaa", agent_api: 1 },
  server: { sha: "line-aaaa", api: 1 },
  paired: true,
  ...over,
});

describe("reloadOffered", () => {
  it("is silent while nothing moved", () => {
    const b = baselineOf(report());
    expect(reloadOffered(b, report())).toBe(false);
  });

  it("fires when the SERVER moved under the tab (F20)", () => {
    const b = baselineOf(report());
    expect(reloadOffered(b, report({ server: { sha: "line-bbbb", api: 1 } }))).toBe(true);
  });

  it("fires when the BUNDLE moved — a terminal-only swap", () => {
    const b = baselineOf(report());
    expect(reloadOffered(b, report({ terminal: { build: "line-bbbb", agent_api: 1 } }))).toBe(true);
  });

  it("fires when the pair broke (F55/F77) even with both stamps unchanged", () => {
    const b = baselineOf(report());
    expect(reloadOffered(b, report({ paired: false }))).toBe(true);
  });

  it("stays silent while the server is unreachable — the swap's own gap", () => {
    // agent-api is briefly unreachable by construction during a swap. `server: null` is "no
    // reading", never "a different one"; treating it as news would paint the bar a second before
    // every successful swap, which is how a notice stops being read.
    const b = baselineOf(report());
    expect(reloadOffered(b, report({ server: null }))).toBe(false);
  });

  it("stays silent when the FIRST reading had no server half and one appears unchanged", () => {
    const b = baselineOf(report({ server: null }));
    expect(b.sha).toBeNull();
    expect(reloadOffered(b, report())).toBe(false);
  });
});

describe("foldBaseline", () => {
  it("fills an unknown server half from the first answer", () => {
    const b: Baseline = { build: "line-aaaa", sha: null };
    expect(foldBaseline(b, report()).sha).toBe("line-aaaa");
  });

  it("never overwrites a known one — that difference is the news", () => {
    const b: Baseline = { build: "line-aaaa", sha: "line-aaaa" };
    expect(foldBaseline(b, report({ server: { sha: "line-bbbb", api: 1 } })).sha).toBe("line-aaaa");
  });
});

describe("readVersion", () => {
  it("reads a well-formed report", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify(report()), { status: 200 }));
    expect(await readVersion(f as unknown as typeof fetch)).toEqual(report());
  });

  it("answers null — never throws — on a network error, a non-200, or junk", async () => {
    const boom = vi.fn(async () => { throw new Error("fetch failed"); });
    expect(await readVersion(boom as unknown as typeof fetch)).toBeNull();
    const five = vi.fn(async () => new Response("nope", { status: 502 }));
    expect(await readVersion(five as unknown as typeof fetch)).toBeNull();
    const junk = vi.fn(async () => new Response(JSON.stringify({ hello: "world" }), { status: 200 }));
    expect(await readVersion(junk as unknown as typeof fetch)).toBeNull();
  });

  it("degrades a malformed server half to null rather than dropping the whole reading", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ ...report(), server: { sha: 7 } }), { status: 200 }));
    const r = await readVersion(f as unknown as typeof fetch);
    expect(r?.server).toBeNull();
    expect(r?.terminal.build).toBe("line-aaaa");
  });
});
