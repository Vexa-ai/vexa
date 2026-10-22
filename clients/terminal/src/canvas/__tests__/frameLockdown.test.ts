/** S03T-1 tail: a canvas view cannot carry the data it was shown out through a STUN host, and the
 *  page that frames it refuses the frame's navigation to any other host. `lockFrame` runs in the
 *  frame's realm before any view is compiled. (The browser half — the parent's `frame-src` refusing
 *  a foreign navigation — is witnessed in headless Chromium; see the car 38 evidence.) */
import { describe, expect, it, vi } from "vitest";

function fakeWindow() {
  return { RTCPeerConnection: class {}, webkitRTCPeerConnection: class {}, RTCDataChannel: class {} } as unknown as Window & typeof globalThis;
}

describe("the canvas frame's lockdown", () => {
  it("removes WebRTC, and the view cannot put it back", async () => {
    vi.resetModules();
    const { lockFrame } = await import("../frame/lockdown");
    const win = fakeWindow();
    expect(lockFrame(win)).toEqual({ webrtc: true });
    expect((win as unknown as Record<string, unknown>).RTCPeerConnection).toBeUndefined();
    expect(() => Object.defineProperty(win, "RTCPeerConnection", { value: class {} })).toThrow();
    (win as unknown as Record<string, unknown>).RTCPeerConnection = class {};      // a plain write does nothing
    expect((win as unknown as Record<string, unknown>).RTCPeerConnection).toBeUndefined();
  });

  it("positive control: an unlocked window keeps WebRTC", () => {
    expect(typeof (fakeWindow() as unknown as Record<string, unknown>).RTCPeerConnection).toBe("function");
  });

  it("the frame's app locks the frame before anything else", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(process.cwd(), "src/canvas/frame/CanvasFrame.tsx"), "utf8");
    const lock = src.indexOf("lockFrame();");
    expect(lock).toBeGreaterThan(0);
    expect(src.indexOf("export function CanvasFrame")).toBeGreaterThan(lock);
  });
});
