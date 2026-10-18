"use client";
/** THE ONE INLINE CARD (UI-DECISIONS 7g, founder 2026-10-16). A question or a summary, quick-reply
 *  chips, an optional free-text answer and a primary/secondary pair — the same card for the agent's
 *  questionnaires (a new workspace), a held write's approval, invites, calendar sends and proposals.
 *
 *  A click is the PERSON's attestation: it is sent as their own chat message, from their signed-in
 *  session, through the chat route that refuses every delegated caller — bound to the pending item
 *  by `id` (`[answer <id>] …`). The agent cannot press it for them. Answered once, the card says
 *  what was chosen and stops offering. */
import { useState } from "react";
import { ASK_CHAT_EVENT } from "../platform/events";

export type CardReply = { label: string; reply?: string };
export type InlineCardSpec = {
  id: string;
  question?: string;
  /** a summary: lines, or label/value pairs */
  summary?: (string | [string, string])[];
  chips?: CardReply[];
  /** a free-text answer; a string is its placeholder */
  free?: boolean | string;
  primary?: CardReply;
  secondary?: CardReply;
};

const ID = /^[A-Za-z0-9._:-]{1,80}$/;
const str = (v: unknown, max = 300) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const reply = (v: unknown): CardReply | null => {
  if (typeof v === "string" && v.trim()) return { label: str(v, 80) };
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const label = str(o.label, 80);
    return label ? { label, ...(str(o.reply) ? { reply: str(o.reply) } : {}) } : null;
  }
  return null;
};

/** A card from its JSON (the ```ask fence an agent writes), or null when it is not one. */
export function parseInlineCard(source: string | unknown): InlineCardSpec | null {
  let o: unknown = source;
  if (typeof source === "string") { try { o = JSON.parse(source); } catch { return null; } }
  if (!o || typeof o !== "object") return null;
  const r = o as Record<string, unknown>;
  const id = str(r.id, 80);
  if (!ID.test(id)) return null;
  const spec: InlineCardSpec = { id };
  if (str(r.question)) spec.question = str(r.question);
  if (Array.isArray(r.summary)) spec.summary = r.summary.slice(0, 8).map((x) =>
    Array.isArray(x) ? [str(x[0], 60), str(x[1], 200)] as [string, string] : str(x)).filter((x) => (Array.isArray(x) ? x[0] || x[1] : x));
  if (Array.isArray(r.chips)) spec.chips = r.chips.slice(0, 6).map(reply).filter((x): x is CardReply => !!x);
  if (r.free === true || typeof r.free === "string") spec.free = typeof r.free === "string" ? str(r.free, 80) : true;
  const p = reply(r.primary), s = reply(r.secondary);
  if (p) spec.primary = p;
  if (s) spec.secondary = s;
  if (!spec.question && !spec.summary?.length) return null;
  return spec;
}

/** The person's answer, as their own message, bound to the card. */
export function answerCard(id: string, r: CardReply | string): void {
  const label = typeof r === "string" ? r : r.label;
  const said = typeof r === "string" ? r : r.reply ?? r.label;
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: `[answer ${id}] ${said}`, display: label } }));
}

export function InlineCard({ spec, onAnswer = answerCard }: { spec: InlineCardSpec; onAnswer?: (id: string, r: CardReply | string) => void }) {
  const [chosen, setChosen] = useState<string | null>(null);
  const [text, setText] = useState("");
  const pick = (r: CardReply | string) => {
    if (chosen) return;
    setChosen(typeof r === "string" ? r : r.label);
    onAnswer(spec.id, r);
  };
  // ANSWERED: one quiet line — the question and what was chosen — and nothing left to press
  if (chosen) return (
    <div className="vx-icard" data-inline-card={spec.id} data-answered="">{`${spec.question ?? "Confirm"} → ${chosen}`}</div>
  );
  return (
    <div className="vx-icard" role="group" aria-label={spec.question || "Confirm"} data-inline-card={spec.id}>
      {spec.question && <p className="vx-icard-q">{spec.question}</p>}
      {spec.summary?.length ? <dl className="vx-icard-sum">{spec.summary.map((x, i) => Array.isArray(x)
        ? <div key={i}><dt>{x[0]}</dt><dd>{x[1]}</dd></div> : <div key={i}><dd>{x}</dd></div>)}</dl> : null}
      {spec.chips?.length ? <div className="vx-icard-chips">{spec.chips.map((c) =>
        <button key={c.label} type="button" className="vx-icard-chip" onClick={() => pick(c)}>{c.label}</button>)}</div> : null}
      {spec.free && <form className="vx-icard-free" onSubmit={(e) => { e.preventDefault(); if (text.trim()) pick(text.trim()); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={typeof spec.free === "string" && spec.free ? spec.free : "Or type an answer"} aria-label="Your answer" />
        {text.trim() && <button type="submit" className="vx-icard-send">Send</button>}
      </form>}
      {(spec.primary || spec.secondary) && <div className="vx-icard-acts">
        {spec.primary && <button type="button" className="vx-btn" data-variant="primary" onClick={() => pick(spec.primary!)}>{spec.primary.label}</button>}
        {spec.secondary && <button type="button" className="vx-btn" data-variant="secondary" onClick={() => pick(spec.secondary!)}>{spec.secondary.label}</button>}
      </div>}
    </div>
  );
}
