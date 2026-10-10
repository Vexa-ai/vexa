/** unit.v1's `Fault` on the terminal side (P18 — architecture pass 6, S65).
 *
 *  The chat's fault labels were keyed by hand, with comments pointing back at the Python lists, so a
 *  kind the server learned rendered as its raw identifier and nothing failed. Now
 *  `core/agent/contracts/unit.v1` names every source and kind, `faultWire.ts` is generated from it,
 *  and the label tables are typed by it. These read the contract BY PATH and drive the renderer with
 *  every kind it names and every golden it pins — and drive the chat proxy, the one terminal-side
 *  writer, to show it mints nothing the contract does not know.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { FaultBlock } from "../../workbench/agent-window";
import { faultHeadline, KIND_LABEL, readFault, SOURCE_LABEL, type Fault } from "../faults";
import { FAULT_KINDS, FAULT_SOURCES, isFaultKind } from "../faultWire";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));
import { POST } from "../../app/api/chat/route";

const CONTRACT = join(__dirname, "../../../../../core/agent/contracts/unit.v1");
const schema = JSON.parse(readFileSync(join(CONTRACT, "unit.schema.json"), "utf8")) as {
  $defs: Record<string, { enum?: string[] }>;
};
const pascal = (s: string) => s.split(/[-_]/).map((w) => w[0].toUpperCase() + w.slice(1)).join("");
const SOURCES = schema.$defs.FaultSource.enum as string[];
const KINDS: Record<string, string[]> = Object.fromEntries(
  SOURCES.map((s) => [s, schema.$defs[`${pascal(s)}FaultKind`].enum as string[]]));
const golden = (prefix: string) => readdirSync(join(CONTRACT, "golden"))
  .filter((f) => f.startsWith(`${prefix}.`) && f.endsWith(".json"))
  .map((f) => ({ name: f, body: JSON.parse(readFileSync(join(CONTRACT, "golden", f), "utf8")) as Record<string, unknown> }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("the terminal's fault vocabulary is the contract's", () => {
  it("faultWire.ts carries exactly the contract's sources and kinds", () => {
    expect([...FAULT_SOURCES]).toEqual(SOURCES);
    expect(Object.fromEntries(Object.entries(FAULT_KINDS).map(([s, k]) => [s, [...k]]))).toEqual(KINDS);
  });

  it("every contract source and kind has a label of its own, and no label names anything else", () => {
    expect(Object.keys(SOURCE_LABEL).sort()).toEqual([...SOURCES].sort());
    expect(Object.keys(KIND_LABEL).sort()).toEqual([...new Set(Object.values(KINDS).flat())].sort());
  });
});

describe("every fault the contract pins renders who failed and what kind", () => {
  const cases = SOURCES.flatMap((source) => KINDS[source].map((kind) => [source, kind] as const));
  it.each(cases)("%s · %s", (source, kind) => {
    const f: Fault = { source, kind, status: null };
    expect(faultHeadline(f)).toBe(
      `${SOURCE_LABEL[source as keyof typeof SOURCE_LABEL]} · ${KIND_LABEL[kind as keyof typeof KIND_LABEL]}`);
  });

  it.each(golden("Fault").map((g) => [g.name, g.body] as const))("%s renders as a typed fault block", (_name, body) => {
    const f = readFault(body);
    expect(f).not.toBeNull();
    render(<FaultBlock failed={{ fault: f as Fault }} />);
    const block = screen.getByRole("alert");
    expect(block.getAttribute("data-fault-source")).toBe(body.source);
    expect(block.getAttribute("data-fault-kind")).toBe(body.kind);
    const headline = block.querySelector("[data-fault-headline]")?.textContent ?? "";
    expect(headline).toContain(SOURCE_LABEL[body.source as keyof typeof SOURCE_LABEL]);
    expect(headline).toContain(KIND_LABEL[body.kind as keyof typeof KIND_LABEL]);
  });

  it("a frame golden's fault reads as the same fault", () => {
    for (const g of [...golden("DoneFrame"), ...golden("ErrorFrame"), ...golden("DispatchRefusal")]) {
      if (!g.body.fault) continue;
      expect(readFault(g.body.fault), g.name).toMatchObject({
        source: (g.body.fault as Fault).source, kind: (g.body.fault as Fault).kind });
    }
  });
});

describe("the chat proxy mints only what the contract names", () => {
  const req = () => ({
    text: async () => JSON.stringify({ prompt: "hi", session: "main" }),
    headers: new Headers(),
    signal: new AbortController().signal,
  }) as unknown as import("next/server").NextRequest;
  const firstEvent = async (res: Response) => {
    const line = (await res.text()).split("\n").find((l) => l.startsWith("data: "));
    return JSON.parse((line ?? "data: {}").slice(6)) as { fault?: Fault };
  };

  it.each([
    ["a 500 with nothing typed", () => new Response("Internal Server Error", { status: 500 })],
    ["a 503 with a sentence", () => new Response(JSON.stringify({ detail: "try again" }), { status: 503 })],
    ["a 504", () => new Response("", { status: 504 })],
    ["a gateway that cannot be reached", () => { throw new TypeError("fetch failed"); }],
  ])("%s", async (_what, answer) => {
    vi.stubGlobal("fetch", vi.fn(async () => answer()));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { fault } = await firstEvent(await POST(req()));
    expect(fault).toBeDefined();
    expect(isFaultKind(fault!.source, fault!.kind), `${fault!.source}/${fault!.kind}`).toBe(true);
  });
});
