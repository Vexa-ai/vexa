/** Feature switches (#1783): the terminal reads `GET /api/features` and hides an unproven feature's
 *  entry points. On / off as answered; a name the answer does not carry is OFF (fail closed: a flag
 *  the server does not name is never on); while loading or when the read fails, every name is off. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { featureOn, loadFeatures, resetFeatures, setFeatureOverride, SWITCHABLE, useFeature } from "../features";

function answer(features: Record<string, boolean>) {
  return vi.fn(async (_url: string) => new Response(JSON.stringify({ features, summaries: {} }), { status: 200 }));
}

function Probe({ name }: { name: string }) {
  return <span data-testid="probe">{useFeature(name) ? "on" : "off"}</span>;
}

beforeEach(() => { setFeatureOverride(null); resetFeatures(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setFeatureOverride("all"); resetFeatures(); });

describe("featureOn", () => {
  it("answers on / off as the server said, and OFF for a name it does not carry", async () => {
    vi.stubGlobal("fetch", answer({ calls: true, crm: false }));
    await loadFeatures();
    expect(featureOn("calls")).toBe(true);
    expect(featureOn("crm")).toBe(false);
    expect(featureOn("notifications")).toBe(false);  // absent from the answer → off (fail closed)
    expect(featureOn("something_else")).toBe(false);
  });

  it("fails closed for transcript signals: off when the answer does not name it, on only when it says so", async () => {
    vi.stubGlobal("fetch", answer({ calls: true }));
    await loadFeatures();
    expect(featureOn("transcript_signals")).toBe(false);
    vi.stubGlobal("fetch", answer({ transcript_signals: true }));
    await loadFeatures({ force: true });
    expect(featureOn("transcript_signals")).toBe(true);
  });

  it("an unknown flag stays off: before the answer, after it, when it fails, and under an override", async () => {
    expect(featureOn("not_switchable")).toBe(false);
    vi.stubGlobal("fetch", answer({ calls: true }));
    await loadFeatures();
    expect(featureOn("not_switchable")).toBe(false);
    resetFeatures();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    await loadFeatures();
    expect(featureOn("not_switchable")).toBe(false);
    setFeatureOverride("all");
    expect(featureOn("not_switchable")).toBe(false);
    setFeatureOverride({ calls: false });
    expect(featureOn("not_switchable")).toBe(false);
  });

  it("a server answer that names an unregistered flag on still cannot switch on a name it omits", async () => {
    vi.stubGlobal("fetch", answer({ proto: true }));
    await loadFeatures();
    expect(featureOn("proto")).toBe(true);           // the server named it: its answer stands
    expect(featureOn("rooms")).toBe(false);          // the server did not: off
  });

  it("is off for every name before the answer", () => {
    for (const n of SWITCHABLE) expect(featureOn(n)).toBe(false);
  });

  it("knows desktop_cards: hidden before the answer, never flashing a card entry point", () => {
    expect(SWITCHABLE).toContain("desktop_cards");
    expect(featureOn("desktop_cards")).toBe(false);
  });

  it("stays off for switchable names when the read fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    await loadFeatures();
    expect(featureOn("calls")).toBe(false);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 500 })));
    await loadFeatures();
    expect(featureOn("folder_drop")).toBe(false);
    expect(featureOn("not_switchable")).toBe(false);
  });

  it("reads once for concurrent callers", async () => {
    const f = answer({ calls: true });
    vi.stubGlobal("fetch", f);
    await Promise.all([loadFeatures(), loadFeatures(), loadFeatures()]);
    await loadFeatures();
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toBe("/api/features");
  });

  it("override: 'all' is every name on without a fetch; a map overrides just those names", async () => {
    const f = answer({});
    vi.stubGlobal("fetch", f);
    setFeatureOverride("all");
    await loadFeatures();
    expect(f).not.toHaveBeenCalled();
    expect(featureOn("calls")).toBe(true);
    setFeatureOverride({ calls: false });
    expect(featureOn("calls")).toBe(false);
    expect(featureOn("crm")).toBe(true);
  });
});

describe("explicit features (rooms, home2)", () => {
  it("are never switched on by \"all\" — only by name, like the server's `explicit` ones", () => {
    setFeatureOverride("all");
    expect(featureOn("rooms")).toBe(false);
    expect(featureOn("home2")).toBe(false);
    expect(featureOn("calls")).toBe(true);
    setFeatureOverride({ rooms: true });
    expect(featureOn("rooms")).toBe(true);
    expect(featureOn("home2")).toBe(false);
  });
});

describe("useFeature", () => {
  it("renders off while loading, then the answer", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => { release = res; })));
    render(<Probe name="calls" />);
    expect(screen.getByTestId("probe").textContent).toBe("off");
    await act(async () => { release(new Response(JSON.stringify({ features: { calls: true } }), { status: 200 })); });
    expect(screen.getByTestId("probe").textContent).toBe("on");
  });

  it("renders off when the server switched it off, and stays off when the read fails", async () => {
    vi.stubGlobal("fetch", answer({ calls: false }));
    render(<Probe name="calls" />);
    await act(async () => { await loadFeatures(); });
    expect(screen.getByTestId("probe").textContent).toBe("off");
    cleanup(); resetFeatures();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    render(<Probe name="calls" />);
    await act(async () => { await loadFeatures(); });
    expect(screen.getByTestId("probe").textContent).toBe("off");
  });

  it("renders off for a name the answer does not carry", async () => {
    vi.stubGlobal("fetch", answer({ crm: false }));
    render(<Probe name="calls" />);
    await act(async () => { await loadFeatures(); });
    expect(screen.getByTestId("probe").textContent).toBe("off");
  });

  it("re-renders when the override changes", () => {
    setFeatureOverride("all");
    render(<Probe name="calls" />);
    expect(screen.getByTestId("probe").textContent).toBe("on");
    act(() => setFeatureOverride({ calls: false }));
    expect(screen.getByTestId("probe").textContent).toBe("off");
  });
});
