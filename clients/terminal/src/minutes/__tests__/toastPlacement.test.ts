/** The Undo toast (sonner, `ui-kit/primitives/Toast`) must not carry the class of the older
 *  fixed, centred toast rule (`layout.css` `.vx-toast`): sharing it pushed the sonner toast half
 *  its width past the right edge on wide windows, hiding Undo. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = (p: string) => readFileSync(join(__dirname, "../..", p), "utf8");

describe("the Undo toast keeps to its own corner", () => {
  it("the sonner toast's class is not the centred toast's class", () => {
    const toast = src("ui-kit/primitives/Toast.tsx");
    const cls = /toastOptions=\{\{\s*className:\s*"([^"]+)"/.exec(toast)?.[1];
    expect(cls).toBeTruthy();
    const layout = src("ui-kit/layout/layout.css");
    expect(new RegExp(`\\.${cls}\\s*\\{[^}]*position:\\s*fixed`).test(layout)).toBe(false);
  });
});
