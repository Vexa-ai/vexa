/** JOIN IS OUTLINED, NEVER A SOLID FILL (UI 7r/7x): "Join is outlined with a green live dot". The
 *  room's pre-join Join was a solid green button; it is now the kit's primary Button (accent outline,
 *  tint on hover) with a green live dot, still the one primary action and still named "Join". The
 *  call bar's other go-ahead (`data-tone="join"`, Share and show) loses its fill too. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CallJoinBar } from "../callkit/CallBar";

afterEach(cleanup);
const here = dirname(fileURLToPath(import.meta.url));

describe("the pre-join Join", () => {
  it("is the kit's primary Button with a live dot, named Join, and joins", () => {
    const onJoin = vi.fn();
    render(<CallJoinBar name="Call" transcribed mic onMic={() => {}} onJoin={onJoin} onCancel={() => {}} />);
    const join = screen.getByRole("button", { name: "Join" });
    expect(join.className).toContain("vx-btn");
    expect(join.getAttribute("data-variant")).toBe("primary");
    expect(join.getAttribute("data-call")).toBe("prejoin-join");
    expect(join.querySelector(".vx-cb-livedot")).not.toBeNull();
    expect(screen.getAllByRole("button").filter((b) => b.getAttribute("data-variant") === "primary")).toHaveLength(1);
    fireEvent.click(join);
    expect(onJoin).toHaveBeenCalledTimes(1);
  });

  it("no call-bar rule paints a solid green fill on a button", () => {
    const css = readFileSync(join(here, "../callkit/callkit.css"), "utf8");
    const joinRule = css.match(/\.vx-cb-text\[data-tone="join"\]\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(joinRule).toContain("background: transparent");
    expect(joinRule).not.toMatch(/background:\s*var\(--green\)/);
    // and the kit's primary variant is itself an outline (7r refines the system)
    const kit = readFileSync(join(here, "../../ui-kit/primitives/controls.css"), "utf8");
    expect(kit).toMatch(/\.vx-btn\[data-variant="primary"\]\s*\{[^}]*background:\s*transparent/);
  });
});
