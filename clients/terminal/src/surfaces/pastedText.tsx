"use client";
/** LONG PASTES COLLAPSE (#1783; founder, on a whole web page pasted into the composer that came back
 *  as a giant user bubble). Like a pasted-content chip: past ~15 lines or ~1,500 characters a paste
 *  is not inserted into the text box — it becomes a chip above it ("Pasted text · 214 lines" and the
 *  first line), removable, and opened back into the box with a click to edit it. On send the agent
 *  receives the full text; each paste rides inside `<!--vexa:pasted-->` … `<!--vexa:pasted-end-->`,
 *  so the user's bubble shows their own typed words plus the collapsed chip, expandable on click.
 *  A stored user message with no markers that is just as long (sent before this, or typed) collapses
 *  whole, so history after a reload reads the same way. */
import { useState, type ReactNode } from "react";
import { X } from "lucide-react";

export const PASTE_MAX_LINES = 15;
export const PASTE_MAX_CHARS = 1500;
export const PASTE_OPEN = "<!--vexa:pasted-->";
export const PASTE_CLOSE = "<!--vexa:pasted-end-->";

const lineCount = (t: string) => t.split("\n").length;

/** Is this text long enough to become a chip? */
export function isLongPaste(text: string): boolean {
  return lineCount(text) > PASTE_MAX_LINES || text.length > PASTE_MAX_CHARS;
}

/** The message the agent receives: the typed words, then every paste, whole, between markers. */
export function withPastes(typed: string, pastes: string[]): string {
  const blocks = pastes.map((p) => `${PASTE_OPEN}\n${p}\n${PASTE_CLOSE}`);
  return [typed, ...blocks].filter((x) => x.trim()).join("\n\n");
}

export type MessagePart = { kind: "text"; text: string } | { kind: "paste"; text: string; label: string };

/** A user message as the bubble draws it: typed words, and pastes collapsed. */
export function messageParts(text: string): MessagePart[] {
  const parts: MessagePart[] = [];
  if (text.includes(PASTE_OPEN)) {
    let rest = text;
    while (rest.length) {
      const i = rest.indexOf(PASTE_OPEN);
      if (i < 0) { if (rest.trim()) parts.push({ kind: "text", text: rest.trim() }); break; }
      if (rest.slice(0, i).trim()) parts.push({ kind: "text", text: rest.slice(0, i).trim() });
      const body = rest.slice(i + PASTE_OPEN.length);
      const j = body.indexOf(PASTE_CLOSE);
      const pasted = (j < 0 ? body : body.slice(0, j)).replace(/^\n/, "").replace(/\n$/, "");
      parts.push({ kind: "paste", text: pasted, label: "Pasted text" });
      rest = j < 0 ? "" : body.slice(j + PASTE_CLOSE.length);
    }
    return parts;
  }
  if (isLongPaste(text)) return [{ kind: "paste", text, label: "Long message" }];
  return [{ kind: "text", text }];
}

function firstLine(text: string): string {
  return (text.split("\n").find((l) => l.trim()) ?? "").trim().slice(0, 120);
}

/** One collapsed paste: label, size, first line; click opens it. In the composer, × removes it and a
 *  click hands the text back to the box for editing (`onEdit`). */
export function PasteChip({ text, label = "Pasted text", onRemove, onEdit }: {
  text: string; label?: string; onRemove?: () => void; onEdit?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const n = lineCount(text);
  return (
    <div className="vx-paste" data-paste-chip data-open={open ? "" : undefined}>
      <div className="vx-paste-head">
        <button type="button" className="vx-paste-toggle" aria-expanded={onEdit ? undefined : open}
          title={onEdit ? "Edit the pasted text" : open ? "Collapse" : "Show the whole text"}
          onClick={() => (onEdit ? onEdit() : setOpen((o) => !o))}>
          <span className="vx-paste-label">{label} · {n} {n === 1 ? "line" : "lines"}</span>
          <span className="vx-paste-first">{firstLine(text)}</span>
        </button>
        {onRemove && <button type="button" className="vx-paste-x" aria-label={`Remove ${label.toLowerCase()}`} onClick={onRemove}><X size={12} strokeWidth={1.75} /></button>}
      </div>
      {open && <div className="vx-paste-body">{text}</div>}
    </div>
  );
}

/** A user bubble's content: the typed words as `renderText` draws them, pastes as chips. */
export function UserMessage({ text, renderText }: { text: string; renderText: (t: string) => ReactNode }) {
  return <>{messageParts(text).map((p, i) => p.kind === "text"
    ? <div key={i}>{renderText(p.text)}</div>
    : <PasteChip key={i} text={p.text} label={p.label} />)}</>;
}
