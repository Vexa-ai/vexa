/** Founder 2026-10-16: "Background blur couldn't start here — your camera is on without it." stood at
 *  the top of the home with no call happening. The notice is said once, inside the call, and goes. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { BlurNotice } from "../BlurButton";
import { blurState, clearBlurNotice, showBlurNoticeForTest } from "../callBlur";

afterEach(() => { cleanup(); clearBlurNotice(); vi.useRealTimers(); });

describe("BlurNotice", () => {
  it("shows once and clears itself", () => {
    vi.useFakeTimers();
    act(() => showBlurNoticeForTest("Background blur couldn't start here — your camera is on without it."));
    const { container } = render(<BlurNotice />);
    expect(container.querySelector("[data-blur-notice]")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(9000); });
    expect(container.querySelector("[data-blur-notice]")).toBeNull();
    expect(blurState().notice).toBe("");
  });
  it("is gone when the call leaves the screen", () => {
    act(() => showBlurNoticeForTest("x"));
    const { unmount } = render(<BlurNotice />);
    unmount();
    expect(blurState().notice).toBe("");
  });
});
