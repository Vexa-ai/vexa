/** Background blur in a call (founder 2026-10-15): the processor is attached and detached on the
 *  camera track, the choice is kept per person, and a browser or device that cannot run it falls
 *  back to the plain camera with one notice — the video never freezes. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { applyBlur, BLUR_ASSETS, blurState, cycleBlur, readBlur, setBlur, SLOW_FOR_MS, type BlurDeps, type FrameStats } from "../callBlur";
import { BlurButton } from "../BlurButton";

function track() {
  let proc: unknown = null;
  return {
    setProcessor: vi.fn(async (p: unknown) => { proc = p; }),
    stopProcessor: vi.fn(async () => { proc = null; }),
    getProcessor: () => proc,
  };
}
function deps(over: Partial<BlurDeps> = {}) {
  let t = 0;
  const timers: (() => void)[] = [];
  const frames: ((s: FrameStats) => void)[] = [];
  const d: BlurDeps & { tick: (ms: number) => void; frame: (ms: number) => void; made: number[] } = {
    made: [],
    supported: () => true,
    make: (radius, onFrame) => { d.made.push(radius); frames.push(onFrame); return { radius }; },
    now: () => t,
    setInterval: (fn) => { timers.push(fn); return timers.length; },
    clearInterval: () => { timers.length = 0; },
    tick: (ms) => { for (let x = 0; x < ms; x += 500) { t += 500; timers.forEach((f) => f()); } },
    frame: (ms) => frames.at(-1)?.({ processingTimeMs: ms }),
    ...over,
  };
  return d;
}

beforeEach(() => { localStorage.clear(); setBlur("off"); });
afterEach(cleanup);

describe("background blur on the camera track", () => {
  it("attaches the processor for light and strong, and detaches it for off", async () => {
    const tr = track(); const d = deps();
    await applyBlur(tr, "light", d);
    expect(tr.setProcessor).toHaveBeenCalledTimes(1);
    expect(d.made).toEqual([6]);
    await applyBlur(tr, "strong", d);
    expect(d.made).toEqual([6, 16]);
    await applyBlur(tr, "off", d);
    expect(tr.stopProcessor).toHaveBeenCalled();
    expect(tr.getProcessor()).toBeNull();
  });

  it("the runtime and model are served by this app, never a CDN", () => {
    expect(BLUR_ASSETS.tasksVisionFileSet).toBe("/mediapipe/wasm");
    expect(BLUR_ASSETS.modelAssetPath.startsWith("/mediapipe/")).toBe(true);
  });

  it("the choice is the person's: kept, read back, cycled off → light → strong → off", () => {
    expect(readBlur()).toBe("off");
    expect(cycleBlur()).toBe("light");
    expect(readBlur()).toBe("light");
    expect(cycleBlur()).toBe("strong");
    expect(cycleBlur()).toBe("off");
    setBlur("strong");
    expect(localStorage.getItem("vexa.call.blur")).toBe("strong");
  });

  it("an unsupported browser: nothing attached, blur off, one notice", async () => {
    setBlur("light");
    const tr = track();
    await applyBlur(tr, "light", deps({ supported: () => false }));
    expect(tr.setProcessor).not.toHaveBeenCalled();
    expect(blurState()).toMatchObject({ level: "off" });
    expect(blurState().notice).toMatch(/isn't supported/);
  });

  it("a processor that cannot start (no model): the camera goes on unprocessed, with a notice", async () => {
    const tr = { ...track(), setProcessor: vi.fn(async () => { throw new Error("404 model"); }) };
    await applyBlur(tr, "light", deps());
    expect(tr.stopProcessor).toHaveBeenCalled();
    expect(blurState().notice).toMatch(/couldn't start/);
  });

  it("sustained slow frames turn it off — never a frozen picture", async () => {
    const tr = track(); const d = deps();
    await applyBlur(tr, "strong", d);
    d.frame(20); d.tick(1000); d.frame(20);
    expect(tr.stopProcessor).not.toHaveBeenCalled();
    for (let i = 0; i < 8; i++) { d.frame(120); d.tick(500); }
    await Promise.resolve(); await Promise.resolve();
    expect(tr.stopProcessor).toHaveBeenCalled();
    expect(blurState()).toMatchObject({ level: "off" });
    expect(blurState().notice).toMatch(/couldn't keep up/);
  });

  it("no frames at all for a while also turns it off", async () => {
    const tr = track(); const d = deps();
    await applyBlur(tr, "light", d);
    d.tick(SLOW_FOR_MS + 500);
    await Promise.resolve(); await Promise.resolve();
    expect(tr.stopProcessor).toHaveBeenCalled();
  });
});

describe("the blur button", () => {
  it("cycles the setting and says what the next press does", () => {
    render(<BlurButton />);
    const b = screen.getByRole("button", { name: /Background blur: Off/ });
    fireEvent.click(b);
    expect(screen.getByRole("button", { name: /Background blur: Light — press for strong/ })).toBeTruthy();
    expect(readBlur()).toBe("light");
  });
});
