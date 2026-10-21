/** S03T-1 — a Meeting Canvas view is workspace-written code, and the terminal page never runs it.
 *
 *  The escape the review named — `({}).constructor.constructor("…")()` — passes the identifier
 *  validator (it names neither `Function` nor `fetch`), so the validator cannot be the boundary.
 *  The boundary is the frame: the page mounts `/canvas-frame` sandboxed to `allow-scripts`, posts
 *  the source across, and evaluates nothing itself. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanvasRuntime } from "../runtime";
import { HarnessActionsProvider } from "../actions";
import { validateViewSource } from "../validator";
import { ACTION_NAMES, CANVAS_FRAME_PATH, CANVAS_FRAME_SANDBOX, readFromFrame, readToFrame, runAction } from "../frame/protocol";
import type { HarnessActions } from "../types";

const ESCAPE = `
export default function View() {
  ({}).constructor.constructor("globalThis.__canvasEscaped = 'ran on the terminal page'")();
  return ui.Empty ? React.createElement(ui.Empty, { title: "hi", body: "there" }) : null;
}
`;

const g = globalThis as { __canvasEscaped?: string };
afterEach(() => { delete g.__canvasEscaped; });

describe("the terminal page never evaluates a view", () => {
  it("the escape passes the validator — so the validator is not the boundary", () => {
    expect(validateViewSource(ESCAPE).ok).toBe(true);
  });

  it("renders the escape into a sandboxed frame and runs none of it here", async () => {
    const { container } = render(<CanvasRuntime source={ESCAPE} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(g.__canvasEscaped).toBeUndefined();
    const frames = container.querySelectorAll("iframe");
    expect(frames).toHaveLength(1);
    const frame = frames[0];
    expect(frame.getAttribute("src")).toBe(CANVAS_FRAME_PATH);
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(CANVAS_FRAME_SANDBOX).not.toMatch(/allow-same-origin|allow-top-navigation|allow-popups|allow-forms/);
  });

  it("no module but the frame's compiler turns text into a function", () => {
    const src = join(process.cwd(), "src");
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n);
      if (statSync(p).isDirectory()) return n === "__tests__" || n === "node_modules" ? [] : walk(p);
      return /\.(ts|tsx|mjs|js)$/.test(n) ? [p] : [];
    });
    const evaluators = walk(src)
      .filter((p) => /new Function\s*\(|\beval\s*\(/.test(readFileSync(p, "utf8").replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, "")))
      .map((p) => relative(src, p));
    expect(evaluators).toEqual(["canvas/frame/compile.ts"]);
  });
});

describe("the frame's wire", () => {
  it("reads only the messages and actions the protocol names", () => {
    expect(readFromFrame({ type: "vexa-canvas:action", name: "fetch", args: [] })).toBeNull();
    expect(readFromFrame({ type: "something-else" })).toBeNull();
    expect(readFromFrame("vexa-canvas:ready")).toBeNull();
    expect(readFromFrame({ type: "vexa-canvas:size", height: 1e9 })).toEqual({ type: "vexa-canvas:size", height: 20000 });
    expect(readToFrame({ type: "vexa-canvas:render", source: 1, state: {} })).toBeNull();
  });

  it("runs an action with each argument coerced to its declared type", () => {
    const calls: unknown[][] = [];
    const actions = Object.fromEntries(ACTION_NAMES.map((n) => [n, (...a: unknown[]) => calls.push([n, ...a])])) as unknown as HarnessActions;
    runAction(actions, "ask", [{ toString: () => "smuggled" }]);
    runAction(actions, "research", [{ name: "Acme", kind: "company", extra: () => 1 }]);
    runAction(actions, "setMetric", ["talk", 3]);
    expect(calls).toEqual([["ask", ""], ["research", { name: "Acme", kind: "company" }], ["setMetric", "talk", 3]]);
  });

  it("the page runs an action only from its own frame's window", () => {
    const ask = vi.fn();
    const actions = Object.fromEntries(ACTION_NAMES.map((n) => [n, n === "ask" ? ask : vi.fn()])) as unknown as HarnessActions;
    render(<HarnessActionsProvider value={actions}><CanvasRuntime source={ESCAPE} /></HarnessActionsProvider>);
    // a message from any other window is dropped before it is read
    window.dispatchEvent(new MessageEvent("message", { data: { type: "vexa-canvas:action", name: "ask", args: ["x"] }, source: window }));
    expect(ask).not.toHaveBeenCalled();
  });
});
