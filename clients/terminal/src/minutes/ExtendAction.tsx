"use client";
/** THE "EXTEND" CONTROLS — two triggers and a landing (PRD decision 32.1).
 *
 *  The page action asks about the whole open page; the floating action asks about what the reader
 *  just highlighted. Both post into the SAME chat and both go through `postIntent`, so there is one
 *  place that decides what a press means and one place that refuses a press it cannot honour.
 *
 *  The panel supplies `workspace` and `path` from THE RESOLVED VIEW SLOT — never from a tab label,
 *  a crumb, or the document header's rendered name (F63). Those are display strings; two of them
 *  have already been wrong on this screen (a folder listing renaming the document behind it), and
 *  an intent built from one sends the agent to work on a file nobody opened.
 */
import type { CSSProperties, RefObject } from "react";
import { useCallback, useEffect, useState } from "react";
import { WORKSPACE_COMMIT_EVENT } from "../canvas/actions";
import { Icon } from "../ui-kit";
import { landPending, postIntent, sourceRange } from "./extend";
import { type as ty, surface } from "./tokens";

/** THE LANDING (decision 32.3). One listener for the whole panel: when the turn commits, the page
 *  the intent named becomes the view. Mounted once by the panel — a second mount would navigate
 *  twice, which is why `landPending` clears before it navigates. */
export function useIntentLanding(): void {
  useEffect(() => {
    const onCommit = () => { landPending(); };
    window.addEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
  }, []);
}

/** ONE CONTROL SHAPE FOR THE TWO ACTS (founder ruling 2026-09-06: *"create this page should also
 *  work in the background and should probably look like extend — same thing, but also creates
 *  file"*).
 *
 *  Extend and Create ask the same chat to do the same kind of work on the same resolved slot, and
 *  on the server they are the same thing again — both are background job kinds in
 *  `chat_intents.JOB_KINDS`, so the chat stays answerable while either one runs. Create said
 *  otherwise on screen: a small plus-chip, a third the size, in the chip type, with no words about
 *  what pressing it would do, standing next to an Extend that is a labelled control. Two shapes
 *  read as two different kinds of thing, and the smaller of them reads as the lesser.
 *
 *  So the shape lives HERE, once, and both acts wear it: an icon, what the act is called, and one
 *  line of what it does — under the content, where the reader arrives with the question. The only
 *  difference left is the one the founder named. Create makes the file, which is what its icon
 *  says; everything else about the two controls is the same object. */
const actBox: CSSProperties = {
  display: "flex", alignItems: "center", gap: 10, width: "100%", marginTop: 28,
  padding: "10px 13px", textAlign: "left", background: surface.raised,
  border: "1px solid var(--line)", borderRadius: 8, cursor: "pointer",
};

function PageAct(p: {
  /** the act, and the handle every test and every walk-through reaches it by */
  act: "extend" | "create";
  icon: "spark" | "plus";
  title: string;
  /** ONE line of what pressing it does. Not two — this is a label, not documentation. */
  line: string;
  hint: string;
  onPress: () => void;
}) {
  return (
    <button data-doc-act={p.act} title={p.hint} onClick={p.onPress} style={actBox}
      onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.borderColor = "var(--accent)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ flex: "none", display: "flex", color: "var(--accent)" }}><Icon name={p.icon} size={15} /></span>
      <span style={{ minWidth: 0 }}>
        <span data-act-title style={{ ...ty.chip, display: "block", fontWeight: 600, color: "var(--t1)" }}>{p.title}</span>
        <span data-act-line style={{ ...ty.meta, display: "block", marginTop: 2 }}>{p.line}</span>
      </span>
    </button>
  );
}

/** THE PAGE ACTION — the open page, whole, UNDER IT (founder ruling 2026-09-06: *"extend button
 *  should be available in the doc body under content, noticeable as one click knowledge
 *  expansion"*).
 *
 *  It was a 14px spark in a row of six glyphs, sized and shaped exactly like Copy — so the one
 *  control on this screen that makes the knowledge GROW asked to be guessed at, and lost. Under the
 *  content is where a reader arrives having finished reading, which is when the question it answers
 *  occurs to them; and it is labelled with what it does, so nobody has to press it to find out.
 *  Same act, same resolved slot (F63) — only its place, its size and its words have moved. */
export function ExtendPageButton(p: { workspace?: string; path: string }) {
  return (
    <PageAct act="extend" icon="spark" hint="Ask this chat to go further on this page"
      title="Extend this page"
      line="Research it, write what is found around it, link both ways."
      onPress={() => postIntent({ kind: "extend", workspace: p.workspace, path: p.path })} />
  );
}

/** THE EMPTY STATE'S ACTION (decision 32.4) — a page that does not exist yet is a thing the chat
 *  can make. The old empty state named the absence and offered nothing; this is the same sentence
 *  with the obvious next move attached, in the shape above and in the same words as its sibling:
 *  the work Create does is Extend's work, on a page that has to be written first. */
export function CreatePageButton(p: { workspace?: string; path: string }) {
  return (
    <PageAct act="create" icon="plus" hint={`Ask this chat to write ${p.path}`}
      title="Create this page"
      line="Research it, write it, link what is found around it both ways."
      onPress={() => postIntent({ kind: "create", workspace: p.workspace, path: p.path })} />
  );
}

interface Hit { text: string; top: number; left: number }

/** THE FLOATING ACTION — appears over a live text selection inside the rendered document, and only
 *  there.
 *
 *  Scoped to the container on purpose: a selection in the conversation, in the rail, or in another
 *  panel is not a selection in this file, and an action that offered to extend it would name this
 *  page while quoting something else. The check is containment in the DOM, not a guess from
 *  coordinates. */
export function SelectionExtend(p: {
  containerRef: RefObject<HTMLElement | null>;
  workspace?: string;
  path: string;
  /** the file source, for locating the selection exactly — see `sourceRange` */
  body: string | null;
}) {
  const [hit, setHit] = useState<Hit | null>(null);

  const read = useCallback(() => {
    const host = p.containerRef.current;
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (!host || !sel || sel.isCollapsed || sel.rangeCount === 0) { setHit(null); return; }
    const text = sel.toString().trim();
    if (!text) { setHit(null); return; }
    const range = sel.getRangeAt(0);
    // BOTH ends inside this document, or it is not this document's selection.
    if (!host.contains(range.startContainer) || !host.contains(range.endContainer)) { setHit(null); return; }
    // jsdom has no layout, so every rect is zeroes there. That is not an error state — the action
    // still belongs on screen, it simply pins to the top-left of the document until a real browser
    // gives it a rect.
    const r = range.getBoundingClientRect?.();
    const hostRect = host.getBoundingClientRect?.();
    const top = r && hostRect ? r.top - hostRect.top + host.scrollTop - 34 : 0;
    const left = r && hostRect ? r.left - hostRect.left : 0;
    setHit({ text, top: Math.max(0, top), left: Math.max(0, left) });
  }, [p.containerRef]);

  useEffect(() => {
    // `selectionchange` is the only event that fires for a keyboard selection and for a
    // drag that ends outside the element; mouseup alone misses both.
    document.addEventListener("selectionchange", read);
    return () => document.removeEventListener("selectionchange", read);
  }, [read]);

  // A new document is a new selection context — never carry the last page's highlight onto it.
  useEffect(() => { setHit(null); }, [p.path, p.workspace]);

  if (!hit) return null;
  return (
    <button data-doc-act="extend-selection" title="Extend — ask this chat to go further on the highlighted text"
      // mousedown, not click: a click on this button would first collapse the selection it is
      // about, and the text would be gone by the time the handler read it.
      onMouseDown={(e) => {
        e.preventDefault();
        postIntent({
          kind: "extend", workspace: p.workspace, path: p.path,
          selection: hit.text, selection_range: sourceRange(p.body, hit.text),
        });
        setHit(null);
      }}
      style={{
        ...ty.chip, position: "absolute", top: hit.top, left: hit.left, zIndex: 4,
        display: "inline-flex", alignItems: "center", gap: 5, color: "var(--t1)",
        background: surface.raisedHi, border: "1px solid var(--line)", borderRadius: 6,
        padding: "3px 8px", cursor: "pointer", boxShadow: "0 2px 8px rgba(0,0,0,.18)",
      }}>
      <Icon name="spark" size={12} />
      Extend
    </button>
  );
}
