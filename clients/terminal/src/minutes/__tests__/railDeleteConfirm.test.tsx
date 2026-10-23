/** The rail's delete confirm keeps its focus across the sessions poll (founder, app.dev
 *  2026-10-11: "delete still does not work for chats"). The rail re-renders on every
 *  `GET /api/sessions` poll (~5 s) with new row objects; the dialog used to re-run its focus setup
 *  on each one and land the reader on Cancel, so Enter cancelled and no DELETE went out. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { useState } from "react";
import { Rail } from "../Rail";
import { Sheet } from "../../ui-kit/layout/Sheet";
import { ConfirmDialog } from "../../ui-kit";
import type { Row } from "../chats";

vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const chat = { key: "c:a", chatId: "a", label: "Inbox", whenLabel: "1:36 PM" } as Row;
const other = { key: "c:b", chatId: "b", label: "Other", whenLabel: "1:30 PM" } as Row;
const base = { hidden: 0, all: true, onAll: () => {}, selKey: "c:a", onSelect: () => {}, onNewChat: () => {} };

// The row's "×" became the row's actions menu (Vexa-ai/vexa#1829): delete is "Delete…" in it, or
// the shortcut. Either way the reader has already chosen the act when the confirm opens.
const openDelete = (label: string) => {
  fireEvent.click(screen.getByRole("button", { name: `Actions for ${label}` }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Delete/ }));
};

describe("the rail delete confirm", () => {
  afterEach(cleanup);

  it("opens on 'Delete chat' (the reader already chose the act), so Enter confirms", () => {
    render(<Rail {...base} rows={[chat]} onDeleteChat={() => {}} />);
    openDelete("Inbox");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete chat" }));
  });

  it("opened by the shortcut, it focuses 'Delete chat' too, so ⌘⌫ then Enter deletes", () => {
    render(<Rail {...base} rows={[chat]} onDeleteChat={() => {}} />);
    fireEvent.keyDown(window, { key: "Backspace", code: "Backspace", ctrlKey: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete chat" }));
  });

  it("a poll re-render does not move focus off the button the reader is on", () => {
    const del = vi.fn();
    const view = render(<Rail {...base} rows={[chat, other]} onDeleteChat={del} />);
    openDelete("Inbox");
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

describe("a dialog keeps its focus across parent renders", () => {
  afterEach(cleanup);

  it("a ConfirmDialog with an inline onCancel keeps focus across parent renders", () => {
    let bump = () => {};
    function Host() {
      const [n, setN] = useState(0); bump = () => setN(n + 1);
      return <ConfirmDialog open title="Delete?" confirmLabel="Delete" consequence={`n=${n}`} onCancel={() => {}} onConfirm={() => {}} />;
    }
    render(<Host />);
    const confirm = screen.getByRole("button", { name: "Delete" });
    confirm.focus();
    act(() => bump()); act(() => bump());
    expect(document.activeElement).toBe(confirm);
  });

  it("a drawer (Sheet) re-rendering does not pull focus out of a dialog open inside it", async () => {
    let bump = () => {};
    function Host() {
      const [n, setN] = useState(0); bump = () => setN(n + 1);
      return <Sheet form="overlay" open onClose={() => {}} label="Chats" side="left" width={240}>
        <button>first in drawer {n}</button>
        <ConfirmDialog open title="Delete?" confirmLabel="Delete" consequence="x" onCancel={() => {}} onConfirm={() => {}} focusConfirm />
      </Sheet>;
    }
    render(<Host />);
    await act(() => new Promise((r) => requestAnimationFrame(() => r(null))));
    const confirm = screen.getByRole("button", { name: "Delete" });
    confirm.focus();
    act(() => bump());
    await act(() => new Promise((r) => requestAnimationFrame(() => r(null))));
    expect(document.activeElement).toBe(confirm);
  });
});
