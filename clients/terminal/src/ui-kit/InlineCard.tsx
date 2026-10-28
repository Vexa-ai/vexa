"use client";
/** THE ONE INLINE CARD (UI-DECISIONS 7g, founder 2026-10-16). A question or a summary, quick-reply
 *  chips, an optional free-text answer and a primary/secondary pair — the same card for the agent's
 *  questionnaires (a new workspace), a held write's approval, invites, calendar sends and proposals.
 *
 *  A click is the PERSON's attestation, BOUND TO A PENDING ITEM (0.13.4 S5): the card opens its
 *  pending item in agent-api (`POST /api/asks/open {session, ask_id}` → nonce + content hash) when it
 *  first renders unanswered, or lazily on the press; the press answers that item with the nonce and
 *  hash (`POST /api/asks/<pending_id>/answer`), and only then is agent-api's returned `message` sent
 *  as the person's own chat message (the chat route refuses every delegated caller). The agent
 *  cannot press it for them, a replay is refused (409), and if opening or answering fails the card
 *  shows a quiet error and sends NOTHING — never the old unbound `[answer <id>] …` text. Answered
 *  once, the card says what was chosen and stops offering.
 *
 *  The person can ALWAYS answer in their own words (founder 2026-10-18): every card draws the text
 *  field, and a typed answer goes as `text` alone — bound like a press (nonce, content hash, single
 *  use, session credential), never posed as a label the card did not offer. When an answer is
 *  refused the card says why, in the server's words, and what was typed stays in the field. */
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { ASK_CHAT_EVENT } from "../platform/events";

export type CardReply = { label: string; reply?: string };
export type InlineCardSpec = {
  id: string;
  question?: string;
  /** a summary: lines, or label/value pairs */
  summary?: (string | [string, string])[];
  chips?: CardReply[];
  /** the text field's placeholder (a string); every card takes a typed answer */
  free?: boolean | string;
  primary?: CardReply;
  secondary?: CardReply;
};

const ID = /^[A-Za-z0-9._:-]{1,80}$/;
/** agent-api's cap on a typed answer (`asks.TEXT_MAX`). */
export const ANSWER_MAX = 4000;
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

/** The chat a card is drawn in: the chat surface provides it, so the card can open its pending item. */
const AskSessionContext = createContext<string | null>(null);
export const AskSessionProvider = AskSessionContext.Provider;

export type PendingAsk = { pending_id: string; nonce: string; content_hash: string; status: "open" | "answered"; answer?: string };
export type Post = { status: number; body: Record<string, unknown> };
async function post(url: string, body: unknown): Promise<Post> {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let b: unknown = {};
    try { b = await r.json(); } catch { b = {}; }
    return { status: r.status, body: b && typeof b === "object" && !Array.isArray(b) ? b as Record<string, unknown> : {} };
  } catch { return { status: 0, body: {} }; }
}

/** The browser's credential cannot answer (signed out, or a key the server could not move onto a
 *  session — hotfix 2026-10-18): the person has to sign in again, which is not "try again". */
export type SignInNeeded = { signIn: true };
function needsSignIn(r: Post): boolean {
  if (r.status === 401) return true;
  const d = r.body.detail as unknown;
  return r.status === 403 && !!d && typeof d === "object" && (d as { refused?: unknown }).refused === "session_required";
}

/** The normal sign-out → sign-in: the page reloads onto the sign-in screen, which returns to this chat. */
export function signInAgain(): void {
  void fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined).finally(() => window.location.reload());
}

/** Why the server refused, in plain words — its own sentence when it gave one, never a bare
 *  "couldn't send". */
const CODES: Record<string, string> = {
  already_answered: "This question was already answered.",
  not_this_card: "This question changed. Check it and answer again.",
  card_changed: "This question changed. Check it and answer again.",
};
export function refusalReason(r: Post): string {
  if (r.status === 0) return "Vexa can't be reached right now. Your answer is still here, so try again in a moment.";
  const d = r.body.detail as unknown;
  let said = "";
  if (typeof d === "string") said = d.trim();
  else if (Array.isArray(d) && d[0] && typeof d[0] === "object" && typeof (d[0] as { msg?: unknown }).msg === "string") said = String((d[0] as { msg: string }).msg);
  else if (d && typeof d === "object" && typeof (d as { note?: unknown }).note === "string") said = String((d as { note: string }).note);
  if (said && CODES[said]) return CODES[said];
  if (said) return /[.!?]$/.test(said) ? said.charAt(0).toUpperCase() + said.slice(1) : `${said.charAt(0).toUpperCase()}${said.slice(1)}.`;
  if (r.status >= 500) return `Vexa had a problem on its side (error ${r.status}). Your answer is still here, so try again in a moment.`;
  return `Vexa refused this answer (error ${r.status}) without saying why.`;
}
export type AskRefused = { refused: string };

/** Open (or find) the pending item behind a card; `refused` says why it couldn't be opened. */
export async function openAsk(session: string, askId: string): Promise<PendingAsk | SignInNeeded | AskRefused> {
  const r = await post("/api/asks/open", { session, ask_id: askId });
  if (needsSignIn(r)) return { signIn: true };
  const b = r.body;
  if (r.status < 200 || r.status >= 300 || typeof b.pending_id !== "string" || typeof b.nonce !== "string" || typeof b.content_hash !== "string") return { refused: refusalReason(r) };
  const a = b.answer as unknown;
  const answer = typeof a === "string" ? a : a && typeof a === "object" && typeof (a as { label?: unknown }).label === "string" ? (a as { label: string }).label : undefined;
  return { pending_id: b.pending_id, nonce: b.nonce, content_hash: b.content_hash, status: b.status === "answered" ? "answered" : "open", ...(answer ? { answer } : {}) };
}

/** Answer the pending item; on success the returned message goes out as the person's own chat message.
 *  A choice goes as `reply`; the person's own words go as `text` alone (never as a label). */
export async function answerAsk(p: PendingAsk, r: CardReply | string): Promise<{ ok: true } | { ok: false; stale: boolean; signIn?: boolean; reason: string }> {
  const label = typeof r === "string" ? r : r.label;
  const answer = typeof r === "string"
    ? { text: r }
    : { reply: { label: r.label, ...(r.reply ? { reply: r.reply } : {}) } };
  const res = await post(`/api/asks/${encodeURIComponent(p.pending_id)}/answer`, { nonce: p.nonce, content_hash: p.content_hash, ...answer });
  if (needsSignIn(res)) return { ok: false, stale: false, signIn: true, reason: "Sign in again to answer" };
  const message = res.body.message;
  if (res.status < 200 || res.status >= 300 || res.body.status !== "answered" || typeof message !== "string" || !message.trim()) {
    const ok2xx = res.status >= 200 && res.status < 300;
    return { ok: false, stale: res.status === 409, reason: ok2xx ? "Vexa took the answer but didn't confirm it. Try again." : refusalReason(res) };
  }
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: message, display: label } }));
  return { ok: true };
}

export function InlineCard({ spec, onAnswer, session: sessionProp }: {
  spec: InlineCardSpec;
  /** a test seam: replaces the bound transport (open + answer) entirely */
  onAnswer?: (id: string, r: CardReply | string) => void;
  session?: string | null;
}) {
  const ctxSession = useContext(AskSessionContext);
  const session = sessionProp ?? ctxSession;
  const [chosen, setChosen] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [signIn, setSignIn] = useState(false);
  const pending = useRef<PendingAsk | null>(null);
  // open the pending item when an unanswered card first renders; an answered one folds at once
  useEffect(() => {
    if (onAnswer || !session) return;
    let on = true;
    void openAsk(session, spec.id).then((p) => {
      if (!on || "signIn" in p || "refused" in p) return;
      pending.current = p;
      if (p.status === "answered") setChosen(p.answer ?? "answered");
    });
    return () => { on = false; };
  }, [onAnswer, session, spec.id]);
  const pick = async (r: CardReply | string) => {
    if (chosen || busy) return;
    if (onAnswer) { setChosen(typeof r === "string" ? r : r.label); onAnswer(spec.id, r); return; }
    setErr(null);
    setSignIn(false);
    if (!session) { setErr("This can't be answered here."); return; }
    setBusy(true);
    const p = pending.current ?? await openAsk(session, spec.id);
    if (p && "signIn" in p) { setBusy(false); setSignIn(true); return; }
    if ("refused" in p) { setBusy(false); setErr(p.refused); return; }
    pending.current = p;
    if (p.status === "answered") { setBusy(false); setChosen(p.answer ?? "answered"); return; }
    const res = await answerAsk(p, r);
    setBusy(false);
    if (res.ok) { setChosen(typeof r === "string" ? r : r.label); return; }
    // a replay or a changed card: drop the nonce so the next press opens the item afresh
    if (res.stale) pending.current = null;
    if (res.signIn) { pending.current = null; setSignIn(true); return; }
    // what was typed stays in the field: nothing is cleared on a refusal
    setErr(res.reason);
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
        <button key={c.label} type="button" className="vx-icard-chip" disabled={busy} onClick={() => void pick(c)}>{c.label}</button>)}</div> : null}
      <form className="vx-icard-free" onSubmit={(e) => { e.preventDefault(); if (text.trim() && !busy) void pick(text.trim()); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={ANSWER_MAX} placeholder={typeof spec.free === "string" && spec.free ? spec.free : "Or type an answer"} aria-label="Your answer" />
        {text.trim() && <button type="submit" className="vx-icard-send" disabled={busy}>Send</button>}
      </form>
      {(spec.primary || spec.secondary) && <div className="vx-icard-acts">
        {spec.primary && <button type="button" className="vx-btn" data-variant="primary" disabled={busy} onClick={() => void pick(spec.primary!)}>{spec.primary.label}</button>}
        {spec.secondary && <button type="button" className="vx-btn" data-variant="secondary" disabled={busy} onClick={() => void pick(spec.secondary!)}>{spec.secondary.label}</button>}
      </div>}
      {err && <p className="vx-icard-err" role="alert">{err}</p>}
      {signIn && <div className="vx-icard-err" role="alert" data-sign-in-again="">
        <p>Sign in again to answer</p>
        <button type="button" className="vx-btn" data-variant="primary" onClick={signInAgain}>Sign in again</button>
      </div>}
    </div>
  );
}
