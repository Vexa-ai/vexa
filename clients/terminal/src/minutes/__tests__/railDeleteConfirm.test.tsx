/** The rail's delete confirm keeps its focus across the sessions poll (founder, app.dev
 *  2026-10-11: "delete still does not work for chats"). The rail re-renders on every
 *  `GET /api/sessions` poll (~5 s) with new row objects; the dialog used to re-run its focus setup
 *  on each one and land the reader on Cancel, so Enter cancelled and no DELETE went out. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { Rail } from "../Rail";
import type { Row } from "../chats";

vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const chat = { key: "c:a", chatId: "a", label: "Inbox", whenLabel: "1:36 PM" } as Row;
const other = { key: "c:b", chatId: "b", label: "Other", whenLabel: "1:30 PM" } as Row;
const base = { hidden: 0, all: true, onAll: () => {}, selKey: "c:a", onSelect: () => {}, onNewChat: () => {} };

describe("the rail delete confirm", () => {
  afterEach(cleanup);

  it("opens on 'Delete chat' (the reader already chose the act), so Enter confirms", () => {
    render(<Rail {...base} rows={[chat]} onDeleteChat={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete Inbox" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete chat" }));
  });

  it("a poll re-render does not move focus off the button the reader is on", () => {
    const del = vi.fn();
    const view = render(<Rail {...base} rows={[chat, other]} onDeleteChat={del} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete Inbox" }));
    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    for (let i = 0; i < 3; i++) view.rerender(<Rail {...base} rows={[{ ...chat }, { ...other }]} onDeleteChat={del} />);
    expect(document.activeElement).toBe(cancel);
    const go = screen.getByRole("button", { name: "Delete chat" });
    go.focus();
    view.rerender(<Rail {...base} rows={[{ ...chat }, { ...other }]} onDeleteChat={del} />);
    expect(document.activeElement).toBe(go);
    fireEvent.click(go);
    expect(del).toHaveBeenCalledExactlyOnceWith("a");
  });
});
