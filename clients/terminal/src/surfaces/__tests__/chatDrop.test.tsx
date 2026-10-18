/** FILES INTO A CHAT — dropped anywhere on the panel, pasted, removed, refused, retried.
 *
 *  Founder 2026-10-10: "a nice UI for dropping a file or a screenshot into the chat, and dropping
 *  anywhere on the whole chat panel should accept it". The whole `Chat` is mounted because the
 *  point is WHERE the drop lands: the messages area, not only the composer.
 *
 *  The upload is the attach button's route (`POST /api/workspace/upload`), one XHR per file so each
 *  has its own progress. The XHR is faked; nothing else about the path is.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent, within } from "@testing-library/react";

const stream = vi.hoisted(() => ({ calls: [] as { req: { prompt: string } }[] }));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown) => {
    stream.calls.push({ req: req as never });
    return Promise.resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "1-0" });
  },
}));

import { Chat } from "../chat";
import { middleTruncate, acceptsFile, MAX_ATTACHMENT_BYTES } from "../attachments";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

/** The upload wire, faked: every request waits until the test answers it. */
class FakeXHR {
  static all: FakeXHR[] = [];
  method = ""; url = ""; body: FormData | null = null;
  status = 0; responseText = "";
  upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  aborted = false;
  constructor() { FakeXHR.all.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  send(body: FormData) { this.body = body; }
  abort() { this.aborted = true; this.onabort?.(); }
  progress(loaded: number, total: number) { this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent); }
  respond(status: number, body: unknown) { this.status = status; this.responseText = JSON.stringify(body); this.onload?.(); }
  get file(): File { return this.body!.get("files") as File; }
}

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat() {
  seq += 1;
  const r = render(
    <ServicesProvider container={container()}>
      <Chat params={{ session: `drop-test-${seq}` }} />
    </ServicesProvider>,
  );
  return r.container;
}

const png = (name = "screenshot.png") => new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });
const pdf = (name = "q3-board-report-final-version.pdf") => new File(["%PDF-1.4"], name, { type: "application/pdf" });
const sized = (f: File, size: number) => { Object.defineProperty(f, "size", { value: size }); return f; };

/** A drag carrying files, the shape the browser hands a drop zone. */
const filesDrag = (files: File[]) => ({ dataTransfer: { files, types: ["Files"], dropEffect: "none" } });

const tray = () => screen.queryByRole("list", { name: "Attachments" });
const composer = () => screen.getByPlaceholderText(/Type \/ for skills/) as HTMLTextAreaElement;
/** Somewhere in the MESSAGES area — the empty-state text, far from the composer. */
const messagesArea = async () => screen.findByText(/Ask the agent to record/);

beforeEach(() => {
  stream.calls.length = 0;
  FakeXHR.all = [];
  try { localStorage.clear(); } catch { /* jsdom always has one */ }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${++n}`);
  URL.revokeObjectURL = vi.fn();
  globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) })) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("dropping files anywhere on the chat panel", () => {
  it("shows the overlay over the whole panel without flickering across children, and attaches on drop", async () => {
    const root = mountChat();
    const target = await messagesArea();
    const zone = root.querySelector("[data-chat-drop-zone]") as HTMLElement;

    // into the panel, then into a child of it: one leave (the child's) must not hide the overlay
    fireEvent.dragEnter(zone, filesDrag([]));
    fireEvent.dragEnter(target, filesDrag([]));
    fireEvent.dragLeave(zone, filesDrag([]));
    expect(screen.getByText("Drop files to attach")).toBeTruthy();
    fireEvent.dragLeave(target, filesDrag([]));
    expect(screen.queryByText("Drop files to attach")).toBeNull();

    fireEvent.dragEnter(target, filesDrag([]));
    fireEvent.drop(target, filesDrag([png(), pdf()]));
    expect(screen.queryByText("Drop files to attach")).toBeNull();

    const items = within(tray()!).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].querySelector("img")?.getAttribute("src")).toBe("blob:preview-1");
    // the non-image is a chip with its size and a middle-truncated name that keeps the extension
    expect(items[1].textContent).toContain(middleTruncate("q3-board-report-final-version.pdf"));
    expect(items[1].textContent).toMatch(/\.pdf/);
    // multiple files: one upload each, on the attach button's route
    expect(FakeXHR.all.map((x) => [x.method, x.url, x.file.name])).toEqual([
      ["POST", "/api/workspace/upload", "screenshot.png"],
      ["POST", "/api/workspace/upload", "q3-board-report-final-version.pdf"],
    ]);
  });

  it("ignores a drag that carries text or a link, not files", async () => {
    const root = mountChat();
    await messagesArea();
    const zone = root.querySelector("[data-chat-drop-zone]") as HTMLElement;
    fireEvent.dragEnter(zone, { dataTransfer: { types: ["text/plain", "text/uri-list"], files: [] } });
    expect(screen.queryByText("Drop files to attach")).toBeNull();
    fireEvent.drop(zone, { dataTransfer: { types: ["text/plain"], files: [] } });
    expect(tray()).toBeNull();
  });
});

describe("pasting into the composer", () => {
  it("attaches a pasted screenshot, shows its progress, and sends it with the turn", async () => {
    mountChat();
    await messagesArea();
    const shot = png("image.png");
    fireEvent.paste(composer(), {
      clipboardData: { getData: () => "", items: [{ kind: "file", type: "image/png", getAsFile: () => shot }] },
    });
    const item = within(tray()!).getByRole("listitem");
    expect(item.getAttribute("aria-label")).toMatch(/image\.png, 4 B, uploading/);

    await act(async () => { FakeXHR.all[0].progress(2, 4); });
    expect(item.getAttribute("aria-label")).toMatch(/uploading 50%/);
    await act(async () => { FakeXHR.all[0].respond(200, { files: [{ name: "abc-image.png", path: "assets/abc-image.png" }] }); });
    expect(item.getAttribute("aria-label")).toMatch(/attached$/);

    fireEvent.change(composer(), { target: { value: "what is this?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0].req.prompt).toContain("what is this?");
    expect(stream.calls[0].req.prompt).toContain("assets/abc-image.png");
    await waitFor(() => expect(tray()).toBeNull());
  });

  it("leaves a plain-text paste alone", async () => {
    mountChat();
    await messagesArea();
    fireEvent.paste(composer(), { clipboardData: { getData: () => "A1\tB1", items: [{ kind: "file", type: "image/png", getAsFile: () => png() }] } });
    expect(tray()).toBeNull();
    expect(FakeXHR.all).toHaveLength(0);
  });
});

describe("the attachment tray", () => {
  it("removes an item with its × and from the keyboard, cancelling its upload", async () => {
    mountChat();
    const target = await messagesArea();
    fireEvent.drop(target, filesDrag([png("a.png"), pdf("b.pdf"), pdf("c.pdf")]));
    fireEvent.click(screen.getByRole("button", { name: "Remove a.png" }));
    expect(FakeXHR.all[0].aborted).toBe(true);
    expect(within(tray()!).getAllByRole("listitem")).toHaveLength(2);

    const b = within(tray()!).getAllByRole("listitem")[0];
    b.focus();
    fireEvent.keyDown(b, { key: "Backspace" });
    expect(within(tray()!).getAllByRole("listitem")).toHaveLength(1);
    const c = within(tray()!).getByRole("listitem");
    expect(c.getAttribute("aria-label")).toMatch(/^c\.pdf/);
    fireEvent.keyDown(c, { key: "Delete" });
    expect(tray()).toBeNull();
  });

  it("opens a larger preview from a thumbnail", async () => {
    mountChat();
    const target = await messagesArea();
    fireEvent.drop(target, filesDrag([png("shot.png")]));
    fireEvent.click(within(tray()!).getByAltText("shot.png"));
    const dialog = screen.getByRole("dialog", { name: "Preview of shot.png" });
    expect(dialog.querySelector("img")?.getAttribute("src")).toBe("blob:preview-1");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("refuses a file over 25 MB out loud, uploads nothing for it, and will not send past it", async () => {
    mountChat();
    const target = await messagesArea();
    fireEvent.drop(target, filesDrag([sized(pdf("huge.pdf"), MAX_ATTACHMENT_BYTES + 1)]));
    const item = within(tray()!).getByRole("listitem");
    expect(item.textContent).toMatch(/Too large .* the limit is 25 MB/);
    expect(FakeXHR.all).toHaveLength(0);

    fireEvent.change(composer(), { target: { value: "read this" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/Remove huge\.pdf to send/)).toBeTruthy();
    expect(stream.calls).toHaveLength(0);
  });

  it("refuses an unsupported type out loud", async () => {
    mountChat();
    const target = await messagesArea();
    fireEvent.drop(target, filesDrag([new File(["MZ"], "setup.exe", { type: "application/x-msdownload" })]));
    expect(within(tray()!).getByRole("listitem").textContent).toMatch(/isn't supported/);
    expect(FakeXHR.all).toHaveLength(0);
    expect(acceptsFile(png())).toBe(true);
  });

  it("shows a failed upload with a retry, and the retry sends", async () => {
    mountChat();
    const target = await messagesArea();
    fireEvent.drop(target, filesDrag([pdf("notes.pdf")]));
    await act(async () => { FakeXHR.all[0].respond(500, { detail: "disk full" }); });
    const item = within(tray()!).getByRole("listitem");
    expect(item.textContent).toContain("disk full");

    // a send does not silently go without it
    fireEvent.change(composer(), { target: { value: "summarise" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/notes\.pdf didn't upload — retry or remove it/)).toBeTruthy();
    expect(stream.calls).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Retry uploading notes.pdf" }));
    expect(FakeXHR.all).toHaveLength(2);
    await act(async () => { FakeXHR.all[1].respond(200, { files: [{ name: "x-notes.pdf", path: "uploads/x-notes.pdf" }] }); });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0].req.prompt).toContain("@file:uploads/x-notes.pdf");
  });
});
