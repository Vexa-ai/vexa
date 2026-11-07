"use client";
/** THE NEW-CHAT HOME (#1783) — one ranked stream of `blocks.v1` cards.
 *
 *  The founder-approved mockup: a greeting with the day's state, up to seven cards in a two-column
 *  flow (one column and five cards at phone width), and a grey line naming what was left out. The
 *  cards are drawn by the ONE blocks renderer (`ui-kit` `CardView`) that also draws ```vexa fences
 *  in workspace pages and in agent replies, so a person chip, a dismiss, a peek or an Expand behaves
 *  the same everywhere. This file only lays the stream out; it never switches on a card's kind.
 *
 *  What a button does is the shell's (`BlocksProvider`, set once around the conversation). Here a
 *  dismissed or snoozed card also leaves the stream at once. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useComposerSlot } from "../surfaces/composerSlot";
import { BlocksProvider, CardView, useBlocksHandlers, shownCards, type BlockCard as Card, type HomeDoc } from "../ui-kit";
import "./home.css";
import { PeopleStrip } from "./PeopleStrip";

/** Phone width: one column and five cards. Read from the viewport, so a resize re-lays the stream. */
function usePhone(): boolean {
  const q = "(max-width: 640px)";
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const on = () => setPhone(m.matches);
    on();
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, []);
  return phone;
}

/** How long a dismiss or a snooze can be taken back before it is told to the server. */
export const UNDO_MS = 5000;

export function HomeStream({ doc, starters, people = true }: { doc: HomeDoc; starters?: ReactNode; people?: boolean }) {
  const phone = usePhone();
  const outer = useBlocksHandlers();
  // THE COMPOSER UNDER THE GREETING (founder, UI lab 2026-10-14: "the void in the middle"): the
  // chat's own composer, drawn here, with the starters under it, and the stream right after.
  const composer = useComposerSlot();
  // the home is read from its top: the chat's own "follow the newest" scroll must not land on the end
  const top = useRef<HTMLElement | null>(null);
  useEffect(() => { const t = setTimeout(() => top.current?.scrollIntoView?.({ block: "start" }), 0); return () => clearTimeout(t); }, []);
  const { cards, cut } = shownCards(doc, phone);
  const [gone, setGone] = useState<Set<string>>(new Set());
  // A DISMISS OR SNOOZE CAN BE TAKEN BACK for a few seconds: the card leaves at once, a toast says
  // what happened with Undo, and only when the toast goes is the feedback sent.
  const [toast, setToast] = useState<{ card: Card; text: string; send: () => void } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flush = useRef<(() => void) | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); flush.current?.(); }, []);
  const leave = (c: Card, text: string, send: () => void) => {
    if (timer.current) { clearTimeout(timer.current); flush.current?.(); }
    setGone((g) => new Set(g).add(c.id));
    flush.current = () => { flush.current = null; send(); };
    setToast({ card: c, text, send });
    timer.current = setTimeout(() => { flush.current?.(); setToast(null); }, UNDO_MS);
  };
  const undo = () => {
    if (!toast) return;
    if (timer.current) clearTimeout(timer.current);
    flush.current = null;
    setGone((g) => { const n = new Set(g); n.delete(toast.card.id); return n; });
    setToast(null);
  };
  const handlers = {
    ...outer,
    onDismiss: (c: Card, r: "wrong" | "done" | "not_now") => leave(c, r === "done" ? "Marked done" : r === "wrong" ? "Dismissed — won't suggest this again" : "Hidden for now", () => outer.onDismiss?.(c, r)),
    onSnooze: (c: Card) => leave(c, "Snoozed until tomorrow", () => outer.onSnooze?.(c)),
  };
  // NO "Not shown now: …" FOOTER (founder, live home: "this just does not make sense")
  void cut;
  return (
    <section ref={top} data-home className="vx-home" aria-label="Home">
      {(doc.greeting.title || doc.greeting.text) && (
        <header className="vx-home-greet">
          {doc.greeting.title && <h2>{doc.greeting.title}</h2>}
          {doc.greeting.text && <p>{doc.greeting.text}</p>}
        </header>
      )}
      {composer && <div data-home-composer className="vx-home-composer">{composer}</div>}
      {starters && <div data-home-starters className="vx-home-starters">{starters}</div>}
      {/* EVERYONE YOU HAVE INTERACTED WITH (founder 2026-10-15), whenever there is anyone */}
      {people && <PeopleStrip />}
      <BlocksProvider value={handlers}>
        <div className={`vx-home-stream${phone ? " one" : ""}`} role="list">
          {cards.filter((c) => !gone.has(c.id)).map((c) => <CardView key={c.id} card={c} people={doc.people} role="listitem" />)}
        </div>
      </BlocksProvider>
      {toast && <div role="status" data-home-toast className="vx-home-toast">
        <span>{toast.text}</span>
        <button type="button" data-home-undo onClick={undo}>Undo</button>
      </div>}
    </section>
  );
}
