"use client";
/** THE TRANSCRIPT AS A CLICKABLE SURFACE — chips over the words, and the button that asks for them.
 *
 *  PRD decision 35 (founder, 2026-09-02): the live transcript renders terms as chips, *"solid = has
 *  a page (opens it in the view), dashed = no page yet"*, and a click drops an `explore` into the
 *  open chat — *"just to find out what that is"*.
 *
 *  A SEPARATE LAYER, ON PURPOSE. It renders through the engine's `renderText` seam rather than
 *  inside it, so the transcript stays one renderer with or without terms and the in-product
 *  inference pipeline can be removed from underneath it (decision 34) without touching this.
 *
 *  IT NEVER ASKS FOR ANYTHING BY ITSELF. The terms come off the chat record (`transcriptTerms.ts`);
 *  the only thing here that talks to anyone is the button, and it talks by posting an intent into
 *  the chat the person already has open. There is no timer, no poll, and no fetch — the founder's
 *  correction was explicit that this "does not require any 'processing on'", and a background loop
 *  that highlights unasked is that feature under a different name.
 */
import React, { useMemo } from "react";
import { OPEN_ENTITY_EVENT } from "./actions";
import { splitTextIntoSpans } from "./inlineSpans";
import { postIntent } from "../minutes/extend";
import { termSpans, termsCursor, useTranscriptTerms, type TranscriptTerm } from "./transcriptTerms";

/** SOLID vs DASHED is the whole visual vocabulary (decision 35.2), so it is one object and not a
 *  scattering of inline conditionals: the two states have to stay legibly different, and the next
 *  person to touch this should have to change one place to change that. */
const chip = (known: boolean): React.CSSProperties => ({
  display: "inline", padding: "0 3px", margin: "0 -1px", borderRadius: 4,
  font: "inherit", lineHeight: "inherit", cursor: "pointer",
  background: known ? "var(--accentbg)" : "transparent",
  color: known ? "var(--accent)" : "var(--t1)",
  border: known ? "1px solid transparent" : "1px dashed var(--line2)",
  borderBottom: known ? "1px solid var(--accent)" : "1px dashed var(--t3)",
});

function open(path: string): void {
  if (typeof window === "undefined") return;
  // THE RESOLVER, not a tab (decisions 26.3 and 28.1): the shell's open-entity listener asks the
  // server what this link points at NOW and navigates the view slot. A chip must never mint a tab —
  // the founder counted seven of them after a few clicks.
  window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path } }));
}

/** One chip. A real `<button>`, so Enter and Space work, focus is visible, and a screen reader is
 *  told what it does — the alternative (a styled `<span onClick>`) is a click target that only a
 *  mouse can reach, inside a live region a keyboard user is otherwise reading fine. */
const TermChip = React.memo(function TermChip(
  p: { term: TranscriptTerm; text: string; meeting: string; segment?: string },
) {
  const known = !!p.term.known;
  const what = p.term.kind ? `${p.term.kind} · ` : "";
  return (
    <button
      type="button"
      data-term={p.term.term}
      data-known={known ? "1" : "0"}
      aria-label={known ? `Open the page for ${p.term.term}` : `Find out what ${p.term.term} is`}
      title={known ? `${what}open its page` : `${what}no page yet — ask this chat what it is`}
      onClick={() => {
        if (known && p.term.known?.path) { open(p.term.known.path); return; }
        postIntent({ kind: "explore", term: p.term.term, meeting: p.meeting, segment: p.segment });
      }}
      style={chip(known)}
    >{p.text}</button>
  );
});

/** A segment's text with its terms chipped. Memoised on (text, terms, segment): a live transcript
 *  re-renders on every arriving line, and without this every chip in the room would be rebuilt —
 *  which is visible, because rebuilding a focused chip drops the keyboard focus off it. */
export const TermText = React.memo(function TermText(
  p: { text: string; terms: TranscriptTerm[]; meeting: string; segment?: string },
) {
  const spans = useMemo(() => splitTextIntoSpans(p.text, termSpans(p.terms)), [p.text, p.terms]);
  const byLabel = useMemo(() => {
    const m = new Map<string, TranscriptTerm>();
    for (const t of p.terms) m.set(t.term.toLowerCase(), t);
    return m;
  }, [p.terms]);
  return (
    <>
      {spans.map((s, i) => {
        const t = s.entity ? byLabel.get(s.entity.label.toLowerCase()) : undefined;
        return t
          ? <TermChip key={`${t.term}-${i}`} term={t} text={s.text} meeting={p.meeting} segment={p.segment} />
          : <React.Fragment key={i}>{s.text}</React.Fragment>;
      })}
    </>
  );
});

/** The `renderText` the transcript engine calls per block. A hook, because the terms are a
 *  subscription and the transcript is the only subscriber that matters. Returns `undefined` when
 *  nothing is published, so the engine falls straight through to plain text and a meeting nobody
 *  highlighted costs exactly nothing. */
export function useTermRenderer(meeting: string): ((text: string) => React.ReactNode) | undefined {
  const terms = useTranscriptTerms(meeting);
  return useMemo(
    () => (terms.length ? (text: string) => <TermText text={text} terms={terms} meeting={meeting} /> : undefined),
    [terms, meeting],
  );
}

/** THE HIGHLIGHT BUTTON (founder correction, 2026-09-02): *"we will have a button on transcripts
 *  that will silently request our open chat to deliver the important and new terms to highlight."*
 *
 *  SILENT means the person sees no bubble and gets no reply — `postIntent` marks the turn machinery
 *  and the `highlight` preset tells the agent to say nothing. What they see is chips appearing.
 *
 *  ADDITIVE means it sends the cursor the LAST publish returned, so a second press adds what has
 *  been said since instead of re-listing the room. The client never invents that cursor; it echoes
 *  the server's. */
export function HighlightButton(p: { meeting: string; live?: boolean }) {
  const terms = useTranscriptTerms(p.meeting);
  const n = terms.length;
  return (
    <button
      type="button"
      data-act="highlight"
      title={n ? `Highlight what has been said since — ${n} term${n === 1 ? "" : "s"} already on this transcript`
               : "Highlight the people, companies and projects this meeting has named"}
      onClick={() => postIntent({ kind: "highlight", meeting: p.meeting, since: termsCursor(p.meeting) })}
      style={{
        flex: "none", display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer",
        fontSize: 11.5, lineHeight: 1.2, padding: "3px 9px", borderRadius: 999,
        border: "1px solid var(--line2)", background: "var(--panel2)", color: "var(--t2)",
      }}
    >
      Highlight{n ? ` · ${n}` : ""}
    </button>
  );
}
