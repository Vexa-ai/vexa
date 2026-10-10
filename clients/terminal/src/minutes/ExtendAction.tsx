"use client";
/** Full-page Extend actions run jobs; selected passages prepare a quote in the chat composer. */
import type { CSSProperties, RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ASK_CHAT_EVENT, WORKSPACE_COMMIT_EVENT } from "../platform";
import { actCleared, actWords, useActState } from "../surfaces/actState";
import type { ChatIntent } from "../surfaces/chatIntent";
import { actTarget, isJobIntent } from "../surfaces/jobs";
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

/** What the field says when it is empty — the founder's own framing of it: what to do with the
 *  thing, and optional. */
export const LINE_PLACEHOLDER = "what to do with it (optional)";

/** The two keys, in one sentence, wherever a field is open. Escape firing ANYWAY is the unusual
 *  half, so it is the half that gets said out loud rather than discovered. */
export const LINE_HINT = "Enter to send · Esc to go ahead without a line";

/** THE ONE-LINE FIELD (#1593), shared by every control that fires an act, so a press means the same
 *  thing wherever it happens.
 *
 *  It owns nothing but the text: `onFire` both fires the act and closes the field, because the two
 *  are one event. Two keys, and they BOTH fire — Enter with what was typed, Escape with nothing.
 *  There is deliberately no third path: blur leaves the field standing rather than firing an act
 *  nobody pressed a key for, and there is no Cancel, because the act was already chosen by the
 *  click that opened this. */
export function ActLine(p: { onFire: (instruction?: string) => void; label: string; style?: CSSProperties }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <input
      ref={ref} data-act-field type="text" value={text} aria-label={p.label} title={LINE_HINT}
      placeholder={LINE_PLACEHOLDER} autoComplete="off" spellCheck={false}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); p.onFire(text.trim() || undefined); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); p.onFire(undefined); }
      }}
      className="c-1 bg-none bd-none p-0" style={{ ...ty.chip, width: "100%", minWidth: 0, outline: "none", ...p.style }}
    />
  );
}

/** THE ACT THIS CONTROL FIRED, or nothing (Vexa-ai/vexa#1604).
 *
 *  A control shows the state of ITS OWN press — the target it fired, held here — rather than of
 *  whatever the store happens to know about that page. Two Extends can name the same page (the one
 *  under the body and the one over a selection), the server refuses the second job on that ground,
 *  and a control lighting up for an act nobody pressed on it would be the panel guessing.
 *
 *  It clears itself when the record does. The record's disappearance IS the landing: the file was
 *  written, the commit event has already refreshed the page under this control, and what is left
 *  for the control to do is stop saying anything. */
function useFiredAct(slot: string) {
  const [fired, setFired] = useState<string | null>(null);
  const state = useActState(fired);
  useEffect(() => { if (fired && !state) setFired(null); }, [fired, state]);
  // A NEW SLOT IS A NEW SUBJECT: whatever was running belonged to the page (or the room) that was
  // here before, and its state must not be worn by the one that replaced it.
  useEffect(() => { setFired(null); }, [slot]);
  const remember = useCallback((posted: ChatIntent | null) => {
    setFired(posted && isJobIntent(posted) ? actTarget(posted) : null);
  }, []);
  /** the person is asking for the act again — the failure line goes with the press that answers it.
   *  The store write happens HERE and not inside a `setFired` updater: an updater runs during
   *  render, and a store that notifies its subscribers from inside one sets state on components
   *  that are mid-render. */
  const forget = useCallback(() => {
    if (fired) actCleared(fired);
    setFired(null);
  }, [fired]);
  return { fired, state, remember, forget };
}

const spinner = (size: number): CSSProperties => ({
  width: size, height: size, borderRadius: "50%", border: "1.5px solid var(--line2)",
  borderTopColor: "var(--accent)", flex: "none",
});

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
 *  says; everything else about the two controls is the same object.
 *
 *  …AND SO DOES THE LINE (#1593). The optional field opens INSIDE this box rather than beside it,
 *  keeping the frame the eye is already on so the page does not move under the cursor at the moment
 *  somebody is about to type. One shape, one gesture, both acts — *"the same line belongs on
 *  Create"*.
 *
 *  …AND SO DOES THE WORKING STATE (#1604). Same box again, in place, for the same reason: the answer
 *  to "is it doing anything?" belongs where the question was asked. */
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
  /** what the field is FOR, for a reader who cannot see the box it opened in */
  fieldLabel: string;
  /** fires the act, and hands back the intent that went — the control needs it to recognise its own
   *  job. `null` when nothing was posted (see `normalizeIntent`: an act it cannot honour is refused,
   *  and a control must not spin over a refusal). */
  onFire: (instruction?: string) => ChatIntent | null;
  /** the resolved slot this control belongs to — a page that changes under an open field closes
   *  it, because a line typed about the page you were reading must never fire against the page
   *  that replaced it. */
  slot: string;
}) {
  const [asking, setAsking] = useState(false);
  const { state, remember, forget } = useFiredAct(p.slot);
  useEffect(() => { setAsking(false); }, [p.slot]);
  const fire = (instruction?: string) => { setAsking(false); remember(p.onFire(instruction)); };
  const open = () => { forget(); setAsking(true); };
  const icon = <span className="c-accent" style={{ flex: "none", display: "flex" }}><Icon name={p.icon} size={15} /></span>;

  if (asking) {
    return (
      <div data-doc-act={`${p.act}-line`} style={{ ...actBox, cursor: "text" }}>
        {icon}
        <span style={{ minWidth: 0, flex: "1 1 0%" }}>
          <ActLine onFire={fire} label={p.fieldLabel} style={{ ...ty.chip, fontWeight: 600 }} />
          <span className="mt-0_5" style={{ ...ty.meta, display: "block" }}>{LINE_HINT}</span>
        </span>
      </div>
    );
  }

  // WHILE THE ACT RUNS, THE CONTROL IS THE ACT (#1604). Not a disabled button and not a button with
  // a guard in its handler: there is nothing a second press could ask for that is not already
  // happening, so the control stops being pressable at all. The handle stays, so the thing the
  // reader (and every test) reaches for is still there — it simply does nothing.
  if (state && state.phase !== "failed") {
    const w = actWords(state, p.title);
    return (
      <div data-doc-act={p.act} data-act-state={state.phase} role="status" aria-live="polite" aria-busy="true"
        title={p.hint} style={{ ...actBox, cursor: "default" }}>
        <span className="vx-op-spin" aria-hidden="true" style={spinner(14)} />
        <span style={{ minWidth: 0 }}>
          <span data-act-title className="fw-600 c-1" style={{ ...ty.chip, display: "block" }}>{w.head}</span>
          {/* the line is empty for the first moment of a job — hold its room rather than let the box
              jump the instant the first step arrives */}
          <span data-act-line className="mt-0_5" style={{ ...ty.meta, display: "block", minHeight: 14 }}>{w.line}</span>
        </span>
      </div>
    );
  }

  // IT FAILED: the same control, offered again, carrying one line of what went wrong. The act is
  // still the obvious next move — what changed is that the person now knows why it has not happened.
  const failed = state?.phase === "failed" ? actWords(state, p.title).line : null;
  return (
    <button data-doc-act={p.act} {...(failed ? { "data-act-state": "failed" } : {})} title={p.hint}
      onClick={open} style={actBox}
      onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.borderColor = "var(--accent)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.borderColor = "var(--line)"; }}>
      {failed
        ? <span className="c-danger" style={{ flex: "none", display: "flex" }}><Icon name="alert" size={15} /></span>
        : icon}
      <span style={{ minWidth: 0 }}>
        <span data-act-title className="fw-600 c-1" style={{ ...ty.chip, display: "block" }}>{p.title}</span>
        <span data-act-line className="mt-0_5" style={{ ...ty.meta, display: "block", ...(failed ? { color: "var(--danger)" } : {}) }}>{failed ?? p.line}</span>
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
export function ExtendPageButton(p: { workspace?: string; path: string; meeting?: string }) {
  // A MEETING'S PAGE EXTENDS DIFFERENTLY (Vexa-ai/vexa#1598) — it reads the transcript since the
  // page's own cursor, and it says so on the control so nobody has to press it to find out. The
  // binding comes from the page itself (its widget slot), never from the shell.
  const room = !!p.meeting;
  return (
    <PageAct act="extend" icon="spark"
      hint={room ? "Ask this chat to take in what has been said since last time"
                 : "Ask this chat to go further on this page"}
      title={room ? "Extend this meeting page" : "Extend this page"}
      line={room ? "Take in what has been said since last time, page what it names, link both ways."
                 : "Research it, write what is found around it, link both ways."}
      fieldLabel="What to do with this page (optional)"
      slot={`${p.workspace ?? ""}|${p.path}`}
      onFire={(instruction) => postIntent({
        kind: "extend", workspace: p.workspace, path: p.path, ...(instruction ? { instruction } : {}),
        ...(p.meeting ? { meeting: p.meeting } : {}),
      })} />
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
      fieldLabel="What to put on this page (optional)"
      slot={`${p.workspace ?? ""}|${p.path}`}
      onFire={(instruction) => postIntent({
        kind: "create", workspace: p.workspace, path: p.path, ...(instruction ? { instruction } : {}),
      })} />
  );
}

interface Hit { text: string; top: number; left: number }

/** Prepare a quote in chat; sending remains the user's action. */
export function SelectionAct(p: {
  containerRef: RefObject<HTMLElement | null>;
  act: string;
  slot: string;
  onReference: (selection: string) => void;
}) {
  const [hit, setHit] = useState<Hit | null>(null);
  const read = useCallback(() => {
    const host = p.containerRef.current;
    const sel = window.getSelection();
    if (!host || !sel || sel.isCollapsed || !sel.rangeCount) { setHit(null); return; }
    const range = sel.getRangeAt(0);
    if (!host.contains(range.startContainer) || !host.contains(range.endContainer)) { setHit(null); return; }
    const text = sel.toString().trim();
    if (!text) { setHit(null); return; }
    const r = range.getBoundingClientRect?.();
    const h = host.getBoundingClientRect?.();
    setHit({ text, top: Math.max(0, r && h ? r.top - h.top + host.scrollTop - 34 : 0),
      left: Math.max(0, r && h ? r.left - h.left : 0) });
  }, [p.containerRef]);
  useEffect(() => {
    document.addEventListener("selectionchange", read);
    return () => document.removeEventListener("selectionchange", read);
  }, [read]);
  useEffect(() => { setHit(null); }, [p.slot]);
  if (!hit) return null;
  return <button data-doc-act={p.act} title="Add this quote to your chat"
    onMouseDown={(e) => e.preventDefault()}
    onClick={() => { p.onReference(hit.text); window.getSelection()?.removeAllRanges(); setHit(null); }}
    className="c-1 bd r-md pt-0_5 pr-2 pb-0_5 pl-2" style={{ ...ty.chip, position: "absolute", zIndex: 4, top: hit.top, left: hit.left, display: "inline-flex", alignItems: "center", gap: 5, background: surface.raisedHi, cursor: "pointer", boxShadow: "0 2px 8px rgba(0,0,0,.18)" }}>
    <Icon name="spark" size={12} /> Ask about this
  </button>;
}

export function referenceInChat(selection: string, source: Record<string, unknown>): void {
  const quote = selection.split("\n").map((line) => `> ${line}`).join("\n");
  const label = source.path ? [source.workspace, source.path].filter(Boolean).join("/") : `Meeting ${source.meeting}`;
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: {
    mode: "draft", reference: source, prompt: `Source: ${label}\n\n${quote}\n\n`,
  } }));
}

/** EXTEND ON A PAGE'S SELECTION. What it adds to the control above is the one thing a page knows
 *  and a room does not: which FILE the words are in, and where in its source they sit. */
export function SelectionExtend(p: {
  containerRef: RefObject<HTMLElement | null>;
  workspace?: string;
  path: string;
  /** the file source, for locating the selection exactly — see `sourceRange` */
  body: string | null;
  /** the meeting this PAGE declares, when it declares one (Vexa-ai/vexa#1598). A meeting doc has the
   *  live transcript embedded in it, so a selection here may be a passage of the room — and the act
   *  becomes the meeting-doc variant either way: read since the page cursor, write into its regions.
   *  Distinct from `canvas/TranscriptExtend` (#1596), which is the same control on the transcript
   *  CANVAS and names no file. */
  meeting?: string;
}) {
  return (
    <SelectionAct containerRef={p.containerRef} act="extend-selection"
      slot={`${p.workspace ?? ""}|${p.path}`}
      onReference={(selection) => referenceInChat(selection, {
        workspace: p.workspace, path: p.path,
        selection_range: sourceRange(p.body, selection),
        ...(p.meeting ? { meeting: p.meeting } : {}),
      })} />
  );
}
