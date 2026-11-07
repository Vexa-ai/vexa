"use client";
/** THE DECISION BLOCK (7q; D13; R11) — used by the card sheet and the inline card.
 *
 *  Short, equal buttons for exactly the actions crm-api says THIS viewer may take (`viewer.actions`);
 *  the first is the primary and renders OUTLINED (7r: no solid fills). Any rule or reason sits on its
 *  own line under the buttons, never inside one. "Discuss" is a same-size secondary button after a
 *  small gap.
 *
 *  Every decision is bound to what the person is looking at: the block fetches `nonce` when it is
 *  rendered (and again when the card's revision changes) and posts `decide` with that nonce and
 *  content hash. A 409 means the card changed or the nonce was used: the block refetches the nonce,
 *  asks the caller to re-read the card, and says "This card changed — check it again".
 *
 *  Deliver asks for one evidence reference; Answer for one line; acknowledge / confirm / decide are
 *  one press each. DECLINE IS HELD: pressing it starts a 5 s window with Undo (`holdDecline`) and only
 *  then is the decision sent; Undo cancels it and nothing reaches crm-api. "Always" (the three-way
 *  ask on approvals and policy changes) is shown but disabled until rule cards exist (S8). */
import { useState, type FormEvent } from "react";
import { MessageSquare } from "lucide-react";
import type { CardAction, CoreCard as CardView } from "../../platform/desktop";
import { decide, discussCard, STALE_LINE, type DecideInput } from "./api";
import { holdDecline, undoDecline, useDeclineHeld, useNonce } from "./hooks";
import { ACTION_LABEL, primaryAction, viewerActions } from "./text";
import "./cards.css";

const ORDER: CardAction[] = ["acknowledge", "decide", "confirm", "answer", "deliver", "use"];

export function DecisionBlock({ card, onChanged, rule, discuss = true, discussCount, sticky }: {
  card: CardView;
  /** the card moved (decided, or found stale): re-read it */
  onChanged?: (card: CardView | null) => void;
  /** a rule or reason to show on its own line under the buttons */
  rule?: string | null;
  discuss?: boolean;
  discussCount?: number;
  sticky?: boolean;
}) {
  const acts = viewerActions(card);
  const { nonce, refetch } = useNonce(card.id, card.revision, acts.length > 0);
  const [step, setStep] = useState<"" | "deliver" | "answer">("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState<string | null>(null);
  const declining = useDeclineHeld(card.id);
  if (!acts.length) return null;

  const primary = primaryAction(card);
  const shown = ORDER.filter((a) => acts.includes(a));
  const threeWay = card.kind === "approval" || card.kind === "policy_change";

  const send = async (action: CardAction, extra: Omit<DecideInput, "action"> = {}) => {
    setBusy(true); setLine(null);
    const n = nonce ?? await refetch();
    if (!n) { setBusy(false); setLine("Couldn't check this card just now"); return; }
    const r = await decide(card.id, n, { action, ...extra });
    setBusy(false);
    if (r.ok) { setStep(""); setText(""); onChanged?.(r.card); return; }
    if (r.stale) { void refetch(); onChanged?.(null); }
    setLine(r.stale ? STALE_LINE : r.line);
  };

  const press = (a: CardAction) => {
    if (a === "deliver" || a === "answer") { setStep(step === a ? "" : a); setText(""); return; }
    void send(a);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = text.trim();
    if (!v || !step) return;
    void send(step, step === "deliver" ? { evidence: v } : { answer: v });
  };

  if (declining) return <div className="vx-cs-decide" data-sticky={sticky ? "" : undefined} data-declining="">
    <p className="vx-cs-held" role="status">Declined · sending in a moment
      <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={() => undoDecline(card.id)}>Undo</button></p>
  </div>;

  return <div className={`vx-cs-decide${sticky ? " vx-cs-sticky" : ""}`} data-sticky={sticky ? "" : undefined} data-decision-block={card.id}>
    <div className="vx-cs-acts">
      {shown.map((a) => <button key={a} type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-primary={a === primary ? "" : undefined}
        data-action={a} aria-expanded={a === "deliver" || a === "answer" ? step === a : undefined} disabled={busy} onClick={() => press(a)}>{ACTION_LABEL[a]}</button>)}
      {threeWay && acts.includes("decide") && <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-action="always" disabled
        title="Rules come later" aria-label="Always (rules come later)">Always</button>}
      {acts.includes("decline") && <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-action="decline" disabled={busy}
        onClick={() => holdDecline(card.id, () => void send("decline"), `Declined: ${card.title}`)}>Decline</button>}
      {discuss && <><span className="vx-cs-gap" aria-hidden />
        <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" data-discuss="" onClick={() => discussCard(card.id)}>
          <MessageSquare size={12} aria-hidden />{discussCount ? `Resume chat · ${discussCount}` : "Discuss"}</button></>}
    </div>
    {step && <form className="vx-cs-input" onSubmit={submit}>
      <input value={text} onChange={(e) => setText(e.target.value)} autoFocus
        aria-label={step === "deliver" ? "Evidence" : "Your answer"} placeholder={step === "deliver" ? "Link or reference to what was delivered" : "Your answer"} />
      <button type="submit" className="vx-btn" data-variant="secondary" data-size="sm" data-primary="" disabled={busy || !text.trim()}>Send</button>
    </form>}
    {rule && <p className="vx-cs-rule">{rule}</p>}
    {line && <p className="vx-cs-err" role="alert">{line}</p>}
  </div>;
}
