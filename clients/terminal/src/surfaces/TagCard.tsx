"use client";
/** THE TAG CARD (0.13.4 S4, D21; sharing-tagging §3; the approved mock `/lab?view=room&share=1`).
 *
 *  A mention never silently changes who has access. When a message in a room @-mentions someone who
 *  is not in it, the message is posted and the TAGGER — only the tagger — gets one inline card (7g):
 *    · a workspace member: "Add Ines to #test and tell her?" — Add and tell · Yes, and always for
 *      <workspace> · Just mention;
 *    · anyone else: "Invite … to <workspace> and #test, and tell them?" — Invite and tell · Just
 *      mention, with the line that says why there is no "always".
 *  The server decides the case and the buttons (`POST /api/rooms/{s}/tag`); this only draws them and
 *  sends the click (`POST /api/rooms/{s}/members`). Answered, the card folds to one line. A tag the
 *  tagger's own "always" line already acted on is a receipt with Undo. One primary per card face. */
import { useState } from "react";
import { Check, Lock, X } from "lucide-react";
import { addRoomMember, removeRoomMember, saidOf, type TagAnswer } from "./roomPeopleApi";
import "./tagCard.css";

type Folded = { ok: boolean; line: string };

export function TagCard({ session, answer, onDone }: { session: string; answer: TagAnswer; onDone?: (a: TagAnswer) => void }) {
  const [folded, setFolded] = useState<Folded | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const name = answer.name || "them";
  const first = name.split(" ")[0] || name;

  if (answer.case === "added_by_rule") {
    return <RuleReceipt session={session} answer={answer} first={first} />;
  }
  const card = answer.card;
  if (!card) return null;
  if (folded) {
    return (
      <div className="vx-tagcard" data-folded="" data-state={folded.ok ? "accepted" : "mention"} role="status">
        {folded.ok ? <Check size={12} aria-hidden /> : <X size={12} aria-hidden />}
        <span className="vx-tagcard-k">Invitation</span><span className="vx-tagcard-fold">{folded.line}</span>
      </div>
    );
  }
  const ask = async (always: boolean) => {
    setBusy(true); setErr("");
    try {
      const who = answer.subject ? { subject: answer.subject } : { email: answer.email };
      const out = await addRoomMember(session, { ...who, name: answer.name, always });
      if (out.invited) setFolded({ ok: true, line: `Invited ${first} · they land in this room after one sign-in` });
      else setFolded({ ok: true, line: `Added ${first}${out.rang?.length ? " · ringing into the call" : ""}`
        + (always ? (out.always?.applied ? ` · always for ${answer.workspace_name || "this workspace"}`
          : ` · “always” sent to ${answer.workspace_name || "the workspace"}'s owner to approve`) : "") });
      onDone?.(answer);
    } catch (e) {
      setErr(saidOf(e) || "That didn't go through. Try again.");
    }
    setBusy(false);
  };
  const invite = card.actions.includes("invite");
  return (
    <div className="vx-tagcard" role="group" aria-label={`Invitation: ${card.title}`} data-tag-card={answer.case} data-only-you="">
      <div className="vx-tagcard-head">
        <span className="vx-tagcard-k">Invitation</span>
        <b className="vx-tagcard-title">{card.title}</b>
        <span className="vx-tagcard-only">only you see this</span>
      </div>
      <dl className="vx-tagcard-fields">
        <dt>Who</dt><dd>{card.who}</dd>
        <dt>Gets</dt><dd>{card.gets}</dd>
      </dl>
      <div className="vx-tagcard-acts">
        <button type="button" className="vx-btn" data-variant="primary" data-tag-yes="" disabled={busy}
          onClick={() => void ask(false)}>{invite ? "Invite and tell them" : `Add and tell ${first}`}</button>
        {card.actions.includes("always") && (
          <button type="button" className="vx-btn" data-variant="secondary" data-always="" disabled={busy}
            title={card.always_line} onClick={() => void ask(true)}>
            Yes, and always for {answer.workspace_name || "this workspace"}</button>)}
        <button type="button" className="vx-btn" data-variant="ghost" data-tag-no="" disabled={busy}
          onClick={() => setFolded({ ok: false, line: `Just a mention · ${first} isn't in this room, so isn't told` })}>
          Just mention</button>
      </div>
      {card.never_always && <p className="vx-tagcard-note"><Lock size={11} aria-hidden /> {card.never_always}</p>}
      {err && <p className="vx-tagcard-err" role="alert">{err}</p>}
    </div>
  );
}

function RuleReceipt({ session, answer, first }: { session: string; answer: TagAnswer; first: string }) {
  const [undone, setUndone] = useState(false);
  const [err, setErr] = useState("");
  const undo = async () => {
    if (!answer.subject) return;
    try { await removeRoomMember(session, answer.subject); setUndone(true); }
    catch { setErr("Couldn't undo. Remove them from the members panel."); }
  };
  return (
    <div className="vx-tagcard" data-folded="" data-state={undone ? "undone" : "accepted"} data-rule-receipt="" role="status">
      <Check size={12} aria-hidden />
      <span className="vx-tagcard-fold">{undone ? `${first} was taken out again` : `Added ${first} · your “always” line for this workspace`}</span>
      {!undone && <button type="button" className="vx-btn" data-variant="ghost" onClick={() => void undo()}>Undo</button>}
      {err && <span role="alert" className="vx-tagcard-err">{err}</span>}
    </div>
  );
}

/** The cards waiting for the tagger, under the thread (newest last). */
export function TagCards({ session, answers, onDismiss }: { session: string; answers: TagAnswer[]; onDismiss: (i: number) => void }) {
  if (!answers.length) return null;
  return (
    <div className="vx-tagcards" data-tag-cards="">
      {answers.map((a, i) => (
        <div key={`${a.subject || a.email}-${i}`} className="vx-tagcards-row">
          <TagCard session={session} answer={a} />
          <button type="button" className="vx-tagcards-close" aria-label="Dismiss" onClick={() => onDismiss(i)}><X size={12} aria-hidden /></button>
        </div>
      ))}
    </div>
  );
}
