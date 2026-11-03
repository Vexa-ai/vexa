import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));
import { Rail } from "../Rail";

afterEach(cleanup);

// The rail's meeting row offers the page header's acts (#1783).
it("a meeting row's menu carries the meeting's own acts", () => {
  const stop = vi.fn();
  const row = { key: "m:42", chatId: null, meetingId: "42", label: "Weekly", whenLabel: "Sat" } as any;
  render(<Rail rows={[row]} selKey={null} onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}}
    meetingActsFor={() => [{ id: "stop-bot", label: "Stop bot", onSelect: stop }, { id: "delete", label: "Delete meeting…", danger: true, onSelect: () => {} }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Actions for Weekly" }));
  expect(screen.getByRole("menuitem", { name: /Delete meeting/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("menuitem", { name: /Stop bot/ }));
  expect(stop).toHaveBeenCalled();
});
