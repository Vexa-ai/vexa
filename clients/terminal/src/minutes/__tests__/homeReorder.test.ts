/** Founder 2026-10-16: reordering the home's workspace cards felt heavy; it should feel like Trello.
 *  The order settles locally at once, persists in the background (debounced, no re-fetch of the
 *  home), and rolls back only if the save fails. Keyboard moves work too. */
import { describe, expect, it, vi } from "vitest";
import { createReorder, moved } from "../desktop/home2/homeReorder";

describe("moved", () => {
  it("puts the dragged card before the one it was dropped on", () => {
    expect(moved(["a", "b", "c", "d"], "d", "b")).toEqual(["a", "d", "b", "c"]);
    expect(moved(["a", "b", "c"], "a", "c")).toEqual(["b", "a", "c"]);
  });
  it("moves by one step for the keyboard", () => {
    expect(moved(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moved(["a", "b", "c"], "b", 1)).toEqual(["a", "c", "b"]);
    expect(moved(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
  });
});

describe("createReorder", () => {
  it("settles at once, saves once after the burst, and keeps the order when the save works", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async () => true);
    const seen: string[][] = [];
    const r = createReorder({ save, onChange: (o) => seen.push(o), debounceMs: 300 });
    r.set(["a", "b", "c"]);
    r.move("c", "a");
    r.move("b", -1);
    expect(seen.at(-1)).toEqual(["c", "b", "a"]);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(301);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(["c", "b", "a"], ["a", "b", "c"]);
    expect(seen.at(-1)).toEqual(["c", "b", "a"]);
    vi.useRealTimers();
  });
  it("rolls back when the save fails", async () => {
    vi.useFakeTimers();
    const failed = vi.fn();
    const r = createReorder({ save: async () => false, onChange: () => {}, onFail: failed, debounceMs: 10 });
    r.set(["a", "b"]);
    r.move("b", "a");
    expect(r.order()).toEqual(["b", "a"]);
    await vi.advanceTimersByTimeAsync(11);
    expect(r.order()).toEqual(["a", "b"]);
    expect(failed).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
