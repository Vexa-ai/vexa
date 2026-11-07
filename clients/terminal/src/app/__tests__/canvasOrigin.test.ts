// @vitest-environment node
/** The Meeting Canvas origin is read from one key, checked at start, and absent means "this origin". */
import { describe, expect, it } from "vitest";
import {
  CANVAS_FRAME_PATH, canvasFrameUrl, canvasOrigin, canvasPort, canvasStartupError, canvasStartupWarning, terminalOrigins,
} from "../canvasOrigin.mjs";
import { CANVAS_FRAME_PATH as PROTOCOL_PATH } from "../../canvas/frame/protocol";

const terminal = { NEXTAUTH_URL: "https://app.example.test" };

describe("the canvas origin", () => {
  it("is an http(s) origin, normalised, or nothing", () => {
    expect(canvasOrigin({ TERMINAL_CANVAS_ORIGIN: "https://Canvas.Example.test/" })).toBe("https://canvas.example.test");
    expect(canvasOrigin({ TERMINAL_CANVAS_ORIGIN: "http://localhost:3002" })).toBe("http://localhost:3002");
    for (const bad of ["", "canvas.example.test", "javascript:alert(1)", "https://canvas.example.test/frame",
      "https://canvas.example.test/?x=1", "https://u:p@canvas.example.test", "https://*.example.test", "https://a.test 'self'"]) {
      expect(canvasOrigin({ TERMINAL_CANVAS_ORIGIN: bad }), bad).toBe("");
    }
  });

  it("is framed by the terminal's public origins, NEXTAUTH_URL then TERMINAL_URL, once each", () => {
    expect(terminalOrigins({ NEXTAUTH_URL: "https://app.example.test/x", TERMINAL_URL: "https://app.example.test" }))
      .toEqual(["https://app.example.test"]);
    expect(terminalOrigins({ TERMINAL_URL: "http://localhost:3001" })).toEqual(["http://localhost:3001"]);
    expect(terminalOrigins({ NEXTAUTH_URL: "not a url" })).toEqual([]);
  });

  it("refuses the start when it cannot be served, naming the key", () => {
    expect(canvasStartupError({ ...terminal, TERMINAL_CANVAS_ORIGIN: "canvas.example.test" })).toMatch(/^TERMINAL_CANVAS_ORIGIN must be an http\(s\) origin/);
    expect(canvasStartupError({ TERMINAL_CANVAS_ORIGIN: "https://canvas.example.test" })).toMatch(/NEXTAUTH_URL/);
    expect(canvasStartupError({ ...terminal, TERMINAL_CANVAS_ORIGIN: "https://app.example.test" })).toMatch(/must differ from the terminal's own origin/);
    expect(canvasStartupError({ ...terminal, TERMINAL_CANVAS_PORT: "http" })).toMatch(/^TERMINAL_CANVAS_PORT must be a port/);
    expect(canvasStartupError({ ...terminal, TERMINAL_CANVAS_PORT: "70000" })).toMatch(/^TERMINAL_CANVAS_PORT/);
  });

  it("starts with a separate origin, and warns — but starts — without one", () => {
    const env = { ...terminal, TERMINAL_CANVAS_ORIGIN: "https://canvas.example.test", TERMINAL_CANVAS_PORT: "3002" };
    expect(canvasStartupError(env)).toBeNull();
    expect(canvasStartupWarning(env)).toBeNull();
    expect(canvasPort(env)).toBe(3002);
    expect(canvasStartupError({})).toBeNull();
    expect(canvasStartupError({ TERMINAL_CANVAS_PORT: "3002" })).toBeNull();
    expect(canvasStartupWarning({})).toMatch(/TERMINAL_CANVAS_ORIGIN is unset.*terminal's own origin/);
  });

  it("names the frame on the canvas origin, else on this one, at the protocol's path", () => {
    expect(CANVAS_FRAME_PATH).toBe(PROTOCOL_PATH);
    expect(canvasFrameUrl("https://canvas.example.test")).toBe("https://canvas.example.test/canvas-frame");
    expect(canvasFrameUrl("")).toBe("/canvas-frame");
  });
});
