"use client";
/** THE TRANSCRIPT'S TERMS — what the chat published for this meeting, and how it stays true (PRD decision 35).
 *
 *  Founder: *"an mcp tool that will take the current transcript and list words that should be
 *  highlighted and clickable like all the things we have in the docs and chat… click on a thing and
 *  it's dropped into the chat as a research drop."*
 *
 *  RECORD-DRIVEN (decision 18). This module holds no opinion about what should be highlighted and
 *  never asks anyone: terms arrive as `terms` EVENTS on the chat stream — the harness emits one when
 *  the agent's `transcript_terms` call publishes — and this is the store they land in. The
 *  transcript renders the store. A second reader that derived its OWN list would be a second writer
 *  of the same surface, which is the one invariant this codebase keeps repeating.
 *
 *  AND THE MAP SURVIVES A RELOAD (Vexa-ai/vexa#1595). Founder, mid-meeting with Highlight pressed:
 *  *"we want transcript being attributed with extracted entities when we get highlight — it should
 *  attribute the transcript in an efficient way (no rewrite)"*. An event is a MOMENT: reload the tab
 *  and the transcript was plain text again, because the only copy was this tab's memory. So the same
 *  publish is also stored server-side, append-only per meeting (`control_plane/meeting_terms.py`),
 *  and `loadTerms` reads it on open and after each Highlight. That is NOT a second opinion — it is
 *  the SAME record the event carries, written by the same act, asked for rather than remembered. It
 *  is on the server rather than in browser storage for the same reason: one map, every device.
 *
 *  ADDITIVE BY CONSTRUCTION. Pressing Highlight again sends the CURSOR from last time, so the turn
 *  sees only what has been said since and publishes only what is new. A second event therefore
 *  MERGES: nothing already on screen is removed by a later press, and a term that arrives twice
 *  keeps the newer answer about whether it has a page. This is why an empty publish is a non-event
 *  server-side — an empty event would read as "and now there are none".
 */
import { useEffect, useSyncExternalStore } from "react";
import { entitySlug } from "../ui-kit/docLinks";
import type { SpanEntity } from "./inlineSpans";

/** The chat published this. `known` non-null ⇒ a page exists that THIS reader can open. */
export interface TranscriptTerm {
  term: string;
  /** the entity kind, when the matched page says one. Absent for an unknown term — the mechanical
   *  extractor cannot know what a name IS, and guessing would colour the chip a lie. */
  kind?: string;
  known: { workspace_id?: string; entity_id?: string; path?: string } | null;
  /** where it was said, in the PUBLISHER's id vocabulary. Provenance for the agent; the renderer
   *  re-finds the term in the text it is drawing (the `splitTextIntoSpans` precedent) rather than
   *  joining on these — the gateway's rows and the live SSE do not share an id space. */
  segments?: (string | number)[];
  first_at?: string | number | null;
}

export interface TermsEvent { meeting: string; cursor?: string; terms: TranscriptTerm[] }

/** A `terms` event, re-emitted onto the window by the chat surface — the same seam, and for the
 *  same reason, as ARTIFACT_EVENT: the stream reader owns no state and the shell owns no parsing. */
export const TERMS_EVENT = "vexa:terminal:terms";

interface Entry { terms: TranscriptTerm[]; cursor: string }
const EMPTY: Entry = { terms: [], cursor: "" };

const store = new Map<string, Entry>();
const subs = new Set<() => void>();
const notify = () => subs.forEach((f) => f());

const key = (t: { term: string }) => t.term.trim().toLowerCase();

/** MERGE, never replace (see the header). Pure and exported: the whole additive promise of the
 *  Highlight button is this one function, and it is the kind of thing that is invisible when wrong. */
export function mergeTerms(prev: TranscriptTerm[], next: TranscriptTerm[]): TranscriptTerm[] {
  const out = [...prev];
  const at = new Map(out.map((t, i) => [key(t), i]));
  for (const t of next) {
    const term = String(t?.term ?? "").trim();
    if (!term) continue;
    const row: TranscriptTerm = { ...t, term };
    const i = at.get(key(row));
    if (i == null) { at.set(key(row), out.length); out.push(row); continue; }
    // A LATER ANSWER ABOUT `known` WINS, including a later null: the page could have been deleted,
    // and a chip that stays solid over a page that is gone is the "opens nothing" failure the
    // resolver already refuses. Everything else merges, so a re-publish cannot lose provenance.
    out[i] = { ...out[i], ...row, segments: row.segments ?? out[i].segments };
  }
  return out;
}

/** A page the agent just wrote, offered to every term that is waiting for one.
 *
 *  THE CHIP TURNS SOLID WITHOUT ASKING ANYBODY (decision 35.3). The alternative — re-running the
 *  Highlight turn to re-ask "does it have a page now" — spends a model call to learn something the
 *  artifact event already said. The match is the slug, which is what `entity_upsert` names the file
 *  after and what the term would have matched had the page existed at publish time.
 *
 *  Pure, so the promotion rule is readable in one place. Returns the SAME array when nothing
 *  changed, so a commit elsewhere in the workspace cannot churn the transcript. */
export function promoteWritten(terms: TranscriptTerm[], workspace: string, path: string): TranscriptTerm[] {
  const m = /^kg\/entities\/([^/]+)\/([^/]+)\.md$/.exec(String(path ?? "").trim().replace(/^\/+/, ""));
  if (!m) return terms;
  const [, kind, slug] = m;
  let hit = false;
  const out = terms.map((t) => {
    if (t.known || entitySlug(t.term) !== slug) return t;
    hit = true;
    return { ...t, kind: t.kind ?? kind, known: { workspace_id: workspace || undefined, entity_id: slug, path: `kg/entities/${kind}/${slug}.md` } };
  });
  return hit ? out : terms;
}

export function recordTerms(ev: TermsEvent): void {
  const meeting = String(ev?.meeting ?? "").trim();
  if (!meeting || !Array.isArray(ev?.terms) || !ev.terms.length) return;
  const cur = store.get(meeting) ?? EMPTY;
  store.set(meeting, {
    terms: mergeTerms(cur.terms, ev.terms),
    // The cursor only ever moves FORWARD to whatever the server last issued; an event without one
    // leaves it where it was, so the next Highlight does not silently re-scan the whole room.
    cursor: String(ev.cursor ?? "") || cur.cursor,
  });
  notify();
}

/** A page landed — promote every term it answers, across every meeting on screen. */
export function notePageWritten(workspace: string, path: string): void {
  let changed = false;
  for (const [meeting, entry] of store) {
    const next = promoteWritten(entry.terms, workspace, path);
    if (next !== entry.terms) { store.set(meeting, { ...entry, terms: next }); changed = true; }
  }
  if (changed) notify();
}

/** For tests, and for a signed-out reload: the store is per-tab and deliberately not persisted HERE
 *  — it is a view of the chat record, and the record is on the server. */
export function resetTerms(): void { store.clear(); inFlight.clear(); notify(); }

/** One read per meeting at a time. Both the button and the renderer subscribe to the same meeting,
 *  so without this every transcript would ask twice on open for one answer. */
const inFlight = new Map<string, Promise<void>>();

/** The stored map for one meeting, merged into the store. Never throws, never clears.
 *
 *  A map we could not read costs the transcript its CHIPS, never its text and never the chat — the
 *  same honest degradation `fetchMeetingNotePath` takes for a Minutes tab. Returning silently is
 *  right here precisely because the chips are an annotation: an error banner over a live meeting,
 *  because a decoration did not load, is worse than the decoration missing. */
export function loadTerms(meeting: string, fetcher: typeof fetch = fetch): Promise<void> {
  const id = String(meeting ?? "").trim();
  if (!id) return Promise.resolve();
  const running = inFlight.get(id);
  if (running) return running;
  const done = (async () => {
    try {
      const res = await fetcher(`/api/meeting/terms?meeting_id=${encodeURIComponent(id)}`, { cache: "no-store" });
      if (!res.ok) return;
      const body = await res.json() as Partial<TermsEvent>;
      // `recordTerms` MERGES, so a read that arrives after a live event cannot un-paint it, and an
      // empty stored map (the ordinary answer for a meeting nobody highlighted) is a non-event.
      if (Array.isArray(body?.terms)) recordTerms({ meeting: id, cursor: body.cursor, terms: body.terms });
    } catch {
      /* offline, signed out, or the route is not there yet — the transcript renders regardless */
    }
  })().finally(() => { inFlight.delete(id); });
  inFlight.set(id, done);
  return done;
}

export const termsFor = (meeting: string): TranscriptTerm[] => (store.get(meeting) ?? EMPTY).terms;
/** The `since` the NEXT Highlight sends. Always a value the server issued (decision 35.2). */
export const termsCursor = (meeting: string): string => (store.get(meeting) ?? EMPTY).cursor;

const entryFor = (meeting: string): Entry => store.get(meeting) ?? EMPTY;

/** Subscribe a component to one meeting's published terms.
 *
 *  ON OPEN and ON EACH HIGHLIGHT the stored map is re-read. The event alone would be enough for the
 *  press that is happening now — it carries what this turn published — but only the server holds
 *  what EVERY press published, so the read is what makes a reload, a second tab and a re-opened
 *  meeting show the same chips as the room that has been highlighted four times. */
export function useTranscriptTerms(meeting: string): TranscriptTerm[] {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onTerms = (e: Event) => {
      const detail = (e as CustomEvent<TermsEvent>).detail;
      recordTerms(detail);                                    // paint now, from the event
      if (String(detail?.meeting ?? "") === meeting) void loadTerms(meeting);   // then reconcile
    };
    const onArtifact = (e: Event) => {
      const d = (e as CustomEvent<{ workspace?: string; path?: string }>).detail;
      if (d?.path) notePageWritten(d.workspace ?? "", d.path);
    };
    window.addEventListener(TERMS_EVENT, onTerms);
    window.addEventListener("vexa:terminal:artifact", onArtifact);
    void loadTerms(meeting);
    return () => {
      window.removeEventListener(TERMS_EVENT, onTerms);
      window.removeEventListener("vexa:terminal:artifact", onArtifact);
    };
  }, [meeting]);
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => entryFor(meeting).terms,
    () => entryFor(meeting).terms,
  );
}

/** The terms as the span splitter's vocabulary. `kind` carries the CHIP STATE rather than the
 *  taxonomy — `known` vs `unknown` — because that is the only thing the renderer paints differently
 *  and the entity's own kind is already on the term for the tooltip. */
export function termSpans(terms: TranscriptTerm[]): SpanEntity[] {
  return terms
    .filter((t) => t.term && t.term.trim().length >= 2)
    .map((t) => ({ id: t.term, label: t.term, kind: t.known ? "known" : "unknown", docPath: t.known?.path }));
}
