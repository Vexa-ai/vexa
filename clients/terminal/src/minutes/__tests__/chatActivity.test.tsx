import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { setChatActivity, useChatActive } from "../../surfaces/chatActivity";

function Row({ session, subject }: { session: string; subject?: string }) {
  return <div data-testid={`${subject ?? "me"}-${session}`}>{useChatActive(session, subject) ? "Active" : "Idle"}</div>;
}
afterEach(cleanup);
it("tracks concurrent chats independently and clears completed/failed turns", () => {
  render(<><Row session="one" /><Row session="two" /><Row session="one" subject="other" /></>);
  act(() => {
    setChatActivity("me\0one", { busy: true, jobs: [] });
    setChatActivity("me\0two", { busy: true, jobs: [] });
  });
  expect(screen.getByTestId("me-one").textContent).toBe("Active");
  expect(screen.getByTestId("me-two").textContent).toBe("Active");
  expect(screen.getByTestId("other-one").textContent).toBe("Idle");
  act(() => setChatActivity("me\0one", { busy: false, jobs: [] }));
  expect(screen.getByTestId("me-one").textContent).toBe("Idle");
  expect(screen.getByTestId("me-two").textContent).toBe("Active");
  act(() => setChatActivity("me\0two", { busy: false, jobs: [] }));
});
it("keeps running jobs active after composer is released; queued-only jobs are not active", () => {
  setChatActivity("me\0jobs", { busy: false, jobs: [{}] });
  const view = render(<Row session="jobs" />);
  expect(screen.getByTestId("me-jobs").textContent).toBe("Active");
  view.unmount();
  render(<Row session="jobs" />);
  expect(screen.getByTestId("me-jobs").textContent).toBe("Active");
  act(() => setChatActivity("me\0jobs", { busy: false, jobs: [{ queued: true }] }));
  expect(screen.getByTestId("me-jobs").textContent).toBe("Idle");
});
