import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { RailToast } from "../RailToast";

afterEach(() => { cleanup(); vi.useRealTimers(); });

// A rail error is a calm toast, not raw text over the account card (#1783).
it("says what to do, can be dismissed, and goes by itself", () => {
  vi.useFakeTimers();
  const gone = vi.fn();
  render(<RailToast message="That chat could not be found. Reload the page and try again." onDismiss={gone} />);
  const t = screen.getByRole("alert");
  expect(t.className).toContain("vx-toast");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(gone).toHaveBeenCalledTimes(1);
  act(() => { vi.advanceTimersByTime(8000); });
  expect(gone).toHaveBeenCalledTimes(2);
});
