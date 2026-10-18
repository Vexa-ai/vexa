/** COPY REFERENCE, PASTE, SEND VEXA — in `surfaces/` because the chat (a surface) uses it and
 *  surfaces sit below the shell; `minutes/readme/*` builds on it.
 *
 *   (PRD 0.13.4 D18/D19; UI 7gg, 7ff).
 *
 *  - `copyReference(ref)` puts BOTH on the clipboard: the chip text (`[[kind:id]]`) for a paste inside
 *    Vexa, and the URL (`https://<host>/r/<kind>/<id>[#anchor]`) for anywhere else. Plain text is the
 *    URL (pasted outside it opens the object in context, sign-in by link); the HTML flavour carries
 *    the chip in `data-vexa-ref`, so a paste back into Vexa becomes a live chip.
 *  - `pasteToChip(textarea, text)` turns a pasted Vexa URL into a chip IN TWO UNDO STEPS: the link is
 *    inserted first, then replaced by the chip, so ⌘Z restores the link.
 *  - `meetingLinkIn(text)` finds a meeting link in a paste, so the composer can offer "Send Vexa".
 *  - `nativeShare(...)` uses the phone's share sheet when there is one.
 *  A reference never grants: whoever opens it sees what their own access allows. */
import { parseMeetingInput, type ParsedMeeting } from "./meetingId";
import { toast } from "../ui-kit/primitives/Toast";
import { pastedRefs, refChip, refUrl } from "../ui-kit/universalRefs";

export type RefTarget = { kind: string; id: string; anchor?: string; label?: string };

export function absoluteRefUrl(r: RefTarget): string {
  const base = typeof window !== "undefined" ? window.location.origin : "";
  return refUrl(r.kind, r.id, base, r.anchor);
}

/** What lands on the clipboard (exported for tests). */
export function clipboardPayload(r: RefTarget): { text: string; html: string; chip: string; url: string } {
  const chip = refChip(r.kind, r.id, undefined, r.anchor);
  const url = absoluteRefUrl(r);
  const label = (r.label ?? "").replace(/[<>&"]/g, "") || url;
  return { chip, url, text: url, html: `<a href="${url}" data-vexa-ref="${chip.replace(/"/g, "&quot;")}">${label}</a>` };
}

export async function copyReference(r: RefTarget): Promise<boolean> {
  const p = clipboardPayload(r);
  try {
    const nav = navigator as Navigator & { clipboard?: Clipboard };
    if (nav.clipboard && typeof ClipboardItem !== "undefined" && "write" in nav.clipboard) {
      await nav.clipboard.write([new ClipboardItem({
        "text/plain": new Blob([p.text], { type: "text/plain" }),
        "text/html": new Blob([p.html], { type: "text/html" }),
      })]);
    } else {
      await nav.clipboard.writeText(p.text);
    }
    toast("Reference copied");
    return true;
  } catch {
    toast("Couldn't copy the reference");
    return false;
  }
}

/** A paste's Vexa reference, from the HTML flavour (a copied reference) or the plain text (a URL). */
export function referenceFromPaste(data: Pick<DataTransfer, "getData">): string | null {
  const html = data.getData("text/html") || "";
  const m = /data-vexa-ref="([^"]+)"/.exec(html);
  if (m) return m[1].replace(/&quot;/g, '"');
  return pastedRefs(data.getData("text/plain") || "");
}

/** Insert `raw` (what was pasted), then replace it with `chips` — two native edits, so the first ⌘Z
 *  brings the pasted link back. Falls back to a direct value change where execCommand is missing. */
export function pasteToChip(el: HTMLTextAreaElement | HTMLInputElement, raw: string, chips: string): string {
  el.focus();
  const start = el.selectionStart ?? el.value.length;
  const doc = el.ownerDocument;
  const can = typeof doc.execCommand === "function";
  if (can && doc.execCommand("insertText", false, raw)) {
    el.setSelectionRange(start, start + raw.length);
    doc.execCommand("insertText", false, chips);
    return el.value;
  }
  const end = el.selectionEnd ?? start;
  el.value = el.value.slice(0, start) + chips + el.value.slice(end);
  el.setSelectionRange(start + chips.length, start + chips.length);
  return el.value;
}

/** A meeting link in a paste (Google Meet, Teams, Zoom, Jitsi), for "Send Vexa". */
export function meetingLinkIn(text: string): { url: string; meeting: ParsedMeeting } | null {
  for (const tok of String(text ?? "").split(/\s+/)) {
    if (!/^https?:\/\//i.test(tok)) continue;
    const meeting = parseMeetingInput(tok);
    if (meeting) return { url: tok, meeting };
  }
  return null;
}

/** Send Vexa to a pasted meeting (the same `POST /api/bots` the meeting page uses). */
export async function sendVexa(link: { url: string; meeting: ParsedMeeting }, f: typeof fetch = fetch): Promise<boolean> {
  try {
    const r = await f("/api/bots", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: link.meeting.platform, native_meeting_id: link.meeting.native_meeting_id, meeting_url: link.url }) });
    toast(r.ok ? "Vexa is on its way to the meeting" : "Vexa couldn't be sent to that meeting");
    return r.ok;
  } catch { toast("Vexa couldn't be sent to that meeting"); return false; }
}

/** The phone's share sheet (7ff, phone pass): the URL, never the object's contents. */
export async function nativeShare(r: RefTarget): Promise<boolean> {
  const nav = navigator as Navigator & { share?: (d: ShareData) => Promise<void> };
  if (typeof nav.share !== "function") return false;
  try { await nav.share({ url: absoluteRefUrl(r), ...(r.label ? { title: r.label } : {}) }); return true; } catch { return false; }
}

/** A message's actions on a phone (long-press): the chat raises this; the shell shows the sheet. */
export const MESSAGE_ACTIONS_EVENT = "vexa:terminal:message-actions";
export function raiseMessageActions(target: RefTarget): void {
  window.dispatchEvent(new CustomEvent(MESSAGE_ACTIONS_EVENT, { detail: target }));
}

/** Long-press (touch) → `onLong`; props to spread on the element. */
export function longPressProps(onLong: () => void, ms = 500) {
  let t: number | null = null;
  const clear = () => { if (t !== null) { window.clearTimeout(t); t = null; } };
  return {
    onTouchStart: () => { clear(); t = window.setTimeout(() => { t = null; onLong(); }, ms); },
    onTouchEnd: clear, onTouchMove: clear, onTouchCancel: clear,
  };
}
