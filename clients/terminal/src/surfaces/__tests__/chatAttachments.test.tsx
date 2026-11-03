/** A SENT ATTACHMENT IS A PICTURE, NOT A SENTENCE (founder, 2026-10-13: his pasted screenshot showed
 *  in his own bubble as "Attached files: 9caa6e9e467d2341-Screenshot_….png", and he wanted "a pasted
 *  image avatar"). The bubble draws thumbnails served by the session's own route; history after a
 *  reload draws the same; the words for the agent carry one mark, never the old sentence.
 *
 *  (Harness copied from chatDrop.test.tsx.)
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
import { MessageAttachments, attachmentSrc } from "../attachments";
import { attachmentPrompt } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";

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
      <Chat params={{ session: `att-test-${seq}` }} />
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


const STORED = "assets/9caa6e9e467d2341-Screenshot_2026-10-13_at_15.26.52.png";
const ORIGINAL = "Screenshot 2026-10-13 at 15.26.52.png";

describe("a sent message's attachments", () => {
  it("draws a picture as a thumbnail that opens full size, and a file as a chip with its name and size", () => {
    render(<MessageAttachments items={[
      { name: ORIGINAL, path: STORED, src: attachmentSrc("s1", STORED), type: "image/png", size: 2048 },
      { name: "q3.pdf", path: "uploads/0123456789abcdef-q3.pdf", src: attachmentSrc("s1", "uploads/0123456789abcdef-q3.pdf"), type: "application/pdf", size: 4096, kind: "file" },
    ]} />);
    const img = screen.getByAltText(ORIGINAL) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(`/api/sessions/s1/attachment?path=${encodeURIComponent(STORED)}`);
    fireEvent.click(screen.getByRole("button", { name: `Open ${ORIGINAL}` }));
    const dialog = screen.getByRole("dialog", { name: `Preview of ${ORIGINAL}` });
    expect(dialog.querySelector("img")?.getAttribute("src")).toBe(img.getAttribute("src"));
    const chip = screen.getByTitle("q3.pdf");
    expect(chip.textContent).toContain("q3.pdf");
    expect(chip.textContent).toContain("4 KB");
    expect(document.body.textContent).not.toContain("9caa6e9e467d2341");
  });

  it("the words for the agent carry the mark, never the old sentence", () => {
    const p = attachmentPrompt("what is this?", [{ name: "9caa6e9e467d2341-Screenshot.png", path: STORED, original: ORIGINAL }]);
    expect(p).not.toContain("Attached files");
    expect(p.startsWith("what is this?\n\n<!--vexa:attachments ")).toBe(true);
    expect(JSON.parse(p.slice(p.indexOf("["), p.lastIndexOf("]") + 1))).toEqual([{ path: STORED, name: ORIGINAL }]);
  });
});

describe("sending a pasted screenshot", () => {
  it("shows the screenshot in the person's bubble — no 'Attached files', no hash", async () => {
    const root = mountChat();
    await messagesArea();
    const shot = new File([new Uint8Array([137, 80, 78, 71])], ORIGINAL, { type: "image/png" });
    fireEvent.paste(composer(), {
      clipboardData: { getData: () => "", items: [{ kind: "file", type: "image/png", getAsFile: () => shot }] },
    });
    await act(async () => { FakeXHR.all[0].respond(200, { files: [{ name: STORED.split("/")[1], path: STORED, original: ORIGINAL, size: 4, type: "image/png" }] }); });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));   // a picture alone is a message
    await waitFor(() => expect(stream.calls).toHaveLength(1));
    expect(stream.calls[0].req.prompt).toContain("<!--vexa:attachments ");
    expect(stream.calls[0].req.prompt).not.toContain("Attached files");
    await waitFor(() => expect(root.querySelectorAll("[data-message-attachments] img")).toHaveLength(1));
    const img = root.querySelector("[data-message-attachments] img")!;
    expect(img.getAttribute("alt")).toBe(ORIGINAL);
    expect(img.getAttribute("src")).toMatch(/^\/api\/sessions\/att-test-\d+\/attachment\?path=/);
    expect(root.textContent).not.toContain("Attached files");
    expect(root.textContent).not.toContain("9caa6e9e467d2341");
  });
});

describe("history after a reload", () => {
  it("draws the attachments the server returns as a field", async () => {
    globalThis.fetch = vi.fn(async (u: string) => ({ ok: true, status: 200, json: async () => (
      String(u).includes("/history") ? { turns: [
        { role: "user", text: "composed prompt <!--vexa:attachments [...]-->", user_text: "is this right?",
          attachments: [{ name: ORIGINAL, path: STORED, type: "image/png", kind: "image", size: 70 }] },
        { role: "agent", text: "Yes.", ops: [] },
      ] } : { turns: [], sessions: [] }) })) as unknown as typeof fetch;
    const root = mountChat();
    const img = await screen.findByAltText(ORIGINAL);
    expect(img.getAttribute("src")).toMatch(/\/attachment\?path=assets%2F9caa6e9e467d2341/);
    expect(screen.getByText("is this right?")).toBeTruthy();
    expect(root.textContent).not.toContain("vexa:attachments");
  });
});
