/** FEATURE SWITCHES (#1783) — founder 2026-10-13: "hide features that are unproven, while having those
 *  ready to continue in dev". Each switched-off feature's entry point is absent, and present again
 *  when it is on; the code underneath is untouched. The switch itself is `platform/features`. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("../../surfaces/gatewayWS", async (orig) => {
  const real = await orig<typeof import("../../surfaces/gatewayWS")>();
  return { ...real, onUserFrame: () => () => {}, onGatewayWSConnected: () => () => {} };
});
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));
vi.mock("../../ui-kit/WsLink", () => ({ WorkspaceName: ({ slug }: { slug: string }) => <span>{slug}</span> }));
vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => ({ id: "aaaaaaaaaa", name: slug, kind: "group", slug, access: "readable", writable: true }),
}));

import { setFeatureOverride } from "../../platform/features";
import { CallButton } from "../CallButton";
import { CallDock } from "../CallDock";
import { enterCall, leaveCall } from "../callState";
import { NotificationBell } from "../NotificationBell";
import { NotificationToasts } from "../NotificationToasts";
import { __resetNotifications } from "../notifications";
import { ContextBar } from "../ContextBar";
import { Rail } from "../Rail";
import { applyFolders, type RailFolders } from "../railFolders";
import { BotAccounts } from "../BotAccounts";
import { FolderImportHost } from "../../surfaces/FolderImport";
import { FOLDER_IMPORT_EVENT, type DroppedFolder } from "../../surfaces/folderDrop";
import { CrmRecordPage } from "../../surfaces/crmRecord";
import type { CallJoin } from "../../surfaces/callsApi";

const settle = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); });

function backend(routes: Record<string, unknown>) {
  const seen: string[] = [];
  const fn = vi.fn(async (url: unknown) => {
    const u = String(url);
    seen.push(u);
    const key = Object.keys(routes).find((k) => u.endsWith(k) || u === k);
    return new Response(JSON.stringify(key ? routes[key] : {}), { status: key ? 200 : 404, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return seen;
}

afterEach(() => { leaveCall(); cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setFeatureOverride("all"); });

const CALL = { room: "call-abc123", meeting_id: 41, owner: "bob", url: "wss://calls.test", started: 1, transcribed: true, participants: 1 };

describe("calls", () => {
  const routes = { "/call": { session_id: "s-1", calls: true, call: CALL }, "/proposals": { proposals: [] } };

  it("off: no Call button, and the call is not even read", async () => {
    setFeatureOverride({ calls: false });
    const seen = backend(routes);
    const { container } = render(<CallButton session="s-1" label="Team" />);
    await settle();
    expect(container.textContent).toBe("");
    expect(seen.some((u) => u.includes("/call"))).toBe(false);
  });

  it("on: the Call button offers the live call", async () => {
    setFeatureOverride({ calls: true });
    backend(routes);
    render(<CallButton session="s-1" label="Team" />);
    await waitFor(() => expect(screen.getByText(/Join call · 1/)).toBeTruthy());
  });

  it("off: the dock does not show even with a call held; on: it does", async () => {
    const join: CallJoin = { session_id: "s-1", url: "wss://calls.test", token: "tok", role: "member", call: CALL };
    await enterCall("pchat-1", "Team", {
      getCall: vi.fn(async () => ({ session_id: "s-1", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join),
    } as never);
    setFeatureOverride({ calls: false });
    const { unmount } = render(<CallDock onOpenShared={() => {}} onOpenPrivate={() => {}} />);
    expect(document.querySelector('[data-call="dock"]')).toBeNull();
    unmount();
    setFeatureOverride({ calls: true });
    render(<CallDock onOpenShared={() => {}} onOpenPrivate={() => {}} />);
    expect(document.querySelector('[data-call="dock"]')).not.toBeNull();
  });
});

describe("the call dock is docked and named", () => {
  it("sits in the conversation column — never an overlay over the meeting sidebar — and names the chat, not its id", async () => {
    const join: CallJoin = { session_id: "s-1", url: "wss://calls.test", token: "tok", role: "member", call: CALL };
    await enterCall("pchat-mv8kibvu", "pchat-mv8kibvu", {
      getCall: vi.fn(async () => ({ session_id: "s-1", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join),
    } as never);
    setFeatureOverride({ calls: true });
    render(<CallDock onOpenShared={() => {}} onOpenPrivate={() => {}} nameOf={(sid) => (sid === "pchat-mv8kibvu" ? "AIT team" : "")} />);
    const dock = document.querySelector('[data-call="dock"]') as HTMLElement;
    expect(dock.style.position).not.toBe("fixed");
    expect(dock.getAttribute("aria-label")).toBe("Call: AIT team");
    expect(dock.textContent).not.toContain("Call · ");
    expect(dock.textContent).not.toContain("pchat-mv8kibvu");
  });
});

describe("the call's one name", () => {
  it("a chat still called New chat does not name the call — never 'Call · New chat'", async () => {
    const join: CallJoin = { session_id: "s-1", url: "wss://calls.test", token: "tok", role: "member", call: CALL };
    await enterCall("pchat-new", "New chat", {
      getCall: vi.fn(async () => ({ session_id: "s-1", calls: true, call: CALL })),
      startCall: vi.fn(), joinCall: vi.fn(async () => join),
    } as never);
    setFeatureOverride({ calls: true });
    render(<CallDock onOpenShared={() => {}} onOpenPrivate={() => {}} nameOf={() => "New chat"} />);
    const dock = document.querySelector('[data-call="dock"]') as HTMLElement;
    expect(dock.getAttribute("aria-label")).toBe("Call: Call with Vexa");
    expect(dock.textContent).not.toContain("New chat");
  });
});

describe("notifications", () => {
  const fakeFetch = (seen: string[]) => vi.fn(async (url: string) => {
    seen.push(String(url));
    return new Response(JSON.stringify({ notifications: [], unread: 0 }), { status: 200 });
  }) as unknown as typeof fetch;

  it("off: no bell, no toast stack, nothing read", async () => {
    const seen: string[] = [];
    __resetNotifications(fakeFetch(seen));
    setFeatureOverride({ notifications: false });
    render(<><NotificationBell /><NotificationToasts /></>);
    await settle();
    expect(document.querySelector("[data-notice-bell]")).toBeNull();
    expect(document.querySelector(".vx-toasts")).toBeNull();
    expect(seen).toEqual([]);
  });

  it("on: the bell and the toast stack are there", async () => {
    __resetNotifications(fakeFetch([]));
    setFeatureOverride({ notifications: true });
    render(<><NotificationBell /><NotificationToasts /></>);
    await settle();
    expect(document.querySelector("[data-notice-bell]")).not.toBeNull();
    expect(document.querySelector(".vx-toasts")).not.toBeNull();
  });
});

describe("folder_drop", () => {
  const sel = { kind: "chat" as const, chatId: "c1", label: "A chat", workspaces: ["personal"], target: undefined };
  const folder = (): DroppedFolder => ({ name: "notes", files: [] } as unknown as DroppedFolder);

  it("off: the workspace menu has no \"New workspace from folder…\"; on: it does", async () => {
    setFeatureOverride({ folder_drop: false });
    const { unmount } = render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()}
      onRemoveWorkspace={vi.fn()} onFolderPicked={vi.fn()} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Workspaces for this chat" }));
    expect(document.querySelector('[data-ctx="folder"]')).toBeNull();
    expect(document.querySelector("[data-folder-picker]")).toBeNull();
    unmount();

    setFeatureOverride({ folder_drop: true });
    render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()}
      onRemoveWorkspace={vi.fn()} onFolderPicked={vi.fn()} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Workspaces for this chat" }));
    expect(document.querySelector('[data-ctx="folder"]')?.textContent).toMatch(/New workspace from folder/);
  });

  it("off: the host does not mount — a folder request opens nothing and a window drop is left alone", async () => {
    setFeatureOverride({ folder_drop: false });
    backend({});
    render(<FolderImportHost />);
    await act(async () => { window.dispatchEvent(new CustomEvent(FOLDER_IMPORT_EVENT, { detail: { kind: "folder", folder: folder() } })); });
    await settle();
    expect(screen.queryByRole("dialog", { name: "New workspace from folder" })).toBeNull();
    const over = new Event("dragover", { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown };
    over.dataTransfer = { types: ["Files"] };
    window.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(false);
  });

  it("on: the host answers the same window dragover", () => {
    setFeatureOverride({ folder_drop: true });
    backend({});
    render(<FolderImportHost />);
    const over = new Event("dragover", { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown };
    over.dataTransfer = { types: ["Files"] };
    window.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
  });
});

describe("crm", () => {
  it("off: the record page says the CRM is not switched on, and reads nothing", async () => {
    setFeatureOverride({ crm: false });
    const seen = backend({});
    render(<CrmRecordPage recordId="r-1" />);
    await settle();
    expect(screen.getByText(/not switched on here/)).toBeTruthy();
    expect(seen).toEqual([]);
  });

  it("on: the record page reads the record", async () => {
    setFeatureOverride({ crm: true });
    const seen = backend({ "/api/crm": { id: "r-1", object_type: "company", revision: 1, fields: { name: "A" } } });
    render(<CrmRecordPage recordId="r-1" />);
    await waitFor(() => expect(seen).toContain("/api/crm"));
    expect(screen.queryByText(/not switched on here/)).toBeNull();
  });
});

describe("shared_chats", () => {
  const F: RailFolders = {
    served: true,
    folders: [
      { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
      { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
      { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
    ],
    filings: {}, order: {},
  };
  const row = (key: string) => ({ key, chatId: key.slice(2), label: `Chat ${key}`, whenLabel: "1:00 PM" }) as unknown as Parameters<typeof applyFolders>[0][number];
  const mount = () => {
    const props = { rows: applyFolders(["c:a", "c:b"].map(row), F), folders: F, selKey: "c:a", onSelect: () => {}, onNewChat: () => {},
      onDeleteChat: () => {}, onPin: () => {}, onHide: () => {}, onMoveRows: vi.fn(), onShareChats: vi.fn(), onDeleteChats: vi.fn() };
    render(<Rail {...(props as unknown as Parameters<typeof Rail>[0])} />);
    fireEvent.click(document.querySelector('[data-row-key="c:b"] [data-chat-row]') as HTMLElement, { metaKey: true });
    return screen.getByRole("toolbar", { name: "Selected chats" });
  };

  it("off: choosing chats offers no \"Share…\"", () => {
    setFeatureOverride({ shared_chats: false, notifications: false });
    const bar = mount();
    expect(within(bar).queryByRole("button", { name: "Share…" })).toBeNull();
    expect(within(bar).getByRole("button", { name: "Delete…" })).toBeTruthy();
  });

  it("on: \"Share…\" is offered", () => {
    setFeatureOverride({ shared_chats: true, notifications: false });
    const bar = mount();
    expect(within(bar).getByRole("button", { name: "Share…" })).toBeTruthy();
  });
});

describe("browser_bots", () => {
  it("off: no Bot accounts section and no read of them; on: it reads them", async () => {
    setFeatureOverride({ browser_bots: false });
    const seen = backend({ "/api/bot-accounts": { accounts: [] } });
    const { container, unmount } = render(<BotAccounts />);
    await settle();
    expect(container.textContent).toBe("");
    expect(seen).toEqual([]);
    unmount();
    setFeatureOverride({ browser_bots: true });
    render(<BotAccounts />);
    await waitFor(() => expect(seen).toContain("/api/bot-accounts"));
  });
});
