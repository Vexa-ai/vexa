"use client";
/** `/decide/<card>` — where every nudge's "Open to decide" lands (0.13.4 S6, mock DecidePage).
 *
 *  SIGNED OUT, nothing of the card shows — not its title, not who is waiting (PRD M4/§16: content
 *  stays behind sign-in). With a carried one-time link (`?t=`) the page offers one "Continue" that
 *  goes to the redeem; the sign-in is spent only by that click, never by a mail scanner opening the
 *  link. Without one, an email field asks for a sign-in link that comes back here.
 *
 *  SIGNED IN, the card alone, full width: kind, title, every field in full BEFORE the decision row,
 *  the rule's suggestion on its own line, then the decisions, then where it lives and who sent it.
 *  Rendering it tells crm-api it was seen (`card/seen`), which is what stops the pending email.
 *  Deciding shows "Done", how many more wait, and the next one. A card this person cannot read
 *  answers one sentence: absence, not a reason.
 *
 *  Like `/join`, this route does not render `<App/>`: it must work for somebody not signed in. */
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ago, cardFrom, decidePath, isDestructive, redeemHref, refusedLine, viewOf, waitingAfter, type DecideAction, type DecideCard } from "./decideState";
import "./decide.css";

type Me = { id?: string; email?: string };
type Phase =
  | { k: "loading" }
  | { k: "signin" }
  | { k: "absent" }
  | { k: "card"; card: DecideCard }
  | { k: "done"; title: string; more: number; next: { id: string; title: string } | null;
      undo?: { card: string; back: string; revision: number; label: string } };

async function post(op: string, body: unknown): Promise<{ status: number; body: unknown }> {
  try {
    const r = await fetch(`/api/desktop/card/${op}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  } catch { return { status: 0, body: {} }; }
}

function readCarried(): string {
  if (typeof window === "undefined") return "";
  try { return new URLSearchParams(window.location.search).get("t") || ""; } catch { return ""; }
}

export function DecideLanding({ cardId, carried }: { cardId: string; carried?: string }) {
  const [phase, setPhase] = useState<Phase>({ k: "loading" });
  const [me, setMe] = useState<Me>({});
  const [t] = useState(() => carried ?? readCarried());
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState("");
  const seenFor = useRef("");

  const loadCard = useCallback(async () => {
    const r = await post("read", { card: cardId });
    if (r.status === 401) { setPhase({ k: "signin" }); return; }
    const c = r.status >= 200 && r.status < 300 ? cardFrom(r.body) : null;
    setPhase(c ? { k: "card", card: viewOf(c) } : { k: "absent" });
  }, [cardId]);

  useEffect(() => {
    let on = true;
    void (async () => {
      const r = await fetch("/api/auth/me", { cache: "no-store" }).catch(() => null);
      if (!on) return;
      if (!r || !r.ok) { setPhase({ k: "signin" }); return; }
      const d = (await r.json().catch(() => ({}))) as { id?: unknown; user_id?: unknown; email?: unknown };
      if (!on) return;
      setMe({ id: typeof d.id === "string" ? d.id : typeof d.user_id === "string" ? d.user_id : undefined, email: typeof d.email === "string" ? d.email : undefined });
      // a signed-in visitor holding a leftover one-time link: drop it from the address bar, unspent
      if (typeof window !== "undefined" && window.location.search.includes("t=")) {
        try { window.history.replaceState(window.history.state, "", decidePath(cardId)); } catch { /* */ }
      }
      await loadCard();
    })();
    return () => { on = false; };
  }, [cardId, loadCard]);

  // seen once the card is on screen (best effort: a server without the op simply answers 400/404)
  useEffect(() => {
    if (phase.k !== "card" || seenFor.current === phase.card.id) return;
    seenFor.current = phase.card.id;
    void post("seen", { card: phase.card.id });
  }, [phase]);

  useEffect(() => { if (typeof document !== "undefined") document.title = "Decide · Vexa"; }, []);

  const decide = async (card: DecideCard, a: DecideAction) => {
    if (busy || !a.transition) return;
    setBusy(true); setLine("");
    const r = await post("update", { card: card.id, expected_revision: card.revision, transition: a.transition });
    if (r.status >= 200 && r.status < 300) {
      const l = await post("list", { mine: true });
      const waiting = l.status >= 200 && l.status < 300 ? waitingAfter(l.body, card.id, me) : [];
      const after = cardFrom(r.body);
      const undo = isDestructive(a) && card.state
        ? { card: card.id, back: card.state, label: a.label,
            revision: typeof after?.revision === "number" ? (after.revision as number) : card.revision + 1 }
        : undefined;
      setPhase({ k: "done", title: card.title, more: waiting.length, next: waiting[0] ?? null, undo });
    } else {
      if (r.status === 409) await loadCard();
      if (r.status === 401) setPhase({ k: "signin" });
      setLine(refusedLine(r.status));
    }
    setBusy(false);
  };

  const undoIt = async (u: NonNullable<Extract<Phase, { k: "done" }>["undo"]>) => {
    if (busy) return;
    setBusy(true); setLine("");
    const r = await post("update", { card: u.card, expected_revision: u.revision, transition: u.back });
    if (r.status >= 200 && r.status < 300) await loadCard();
    else setLine(refusedLine(r.status));
    setBusy(false);
  };

  const signOut = () => {
    void fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined).finally(() => {
      try { window.location.assign(decidePath(cardId)); } catch { /* */ }
    });
  };

  if (phase.k === "loading") return <main className="vx-decide" data-landing="loading" aria-busy="true" />;
  if (phase.k === "signin") return <main className="vx-decide" data-landing="signin"><SignIn cardId={cardId} carried={t} /></main>;
  if (phase.k === "absent") return (
    <main className="vx-decide" data-landing="absent">
      <p className="vx-decide-say">There is no card here for you.</p>
      <a className="vx-decide-link" href="/">Open Vexa</a>
    </main>
  );
  if (phase.k === "done") return (
    <main className="vx-decide" data-landing="done">
      <h1 className="vx-decide-title">Done</h1>
      <p className="vx-decide-say">{phase.title}</p>
      {phase.undo && <p className="vx-decide-say" data-undo="">Decided: {phase.undo.label}. <button type="button" className="vx-decide-linkbtn" disabled={busy}
        onClick={() => void undoIt(phase.undo!)}>Undo</button></p>}
      {line && <p className="vx-decide-line" role="status">{line}</p>}
      <p className="vx-decide-say" data-more={phase.more}>{phase.more ? `${phase.more} more waiting on you.` : "Nothing else waits on you."}</p>
      {phase.next && <a className="vx-decide-act" data-next-card={phase.next.id} href={decidePath(phase.next.id)}>Next: {phase.next.title}</a>}
      <a className="vx-decide-link" href="/">Open the Inbox</a>
      <NotYou email={me.email} onSignOut={signOut} />
    </main>
  );

  const c = phase.card;
  const decisions = c.actions.filter((a) => a.transition);
  const elsewhere = c.actions.filter((a) => !a.transition);
  return (
    <main className="vx-decide" data-landing="card">
      <article className="vx-decide-card" data-card={c.id}>
        {c.kind && <p className="vx-decide-kind">{c.kind}</p>}
        <h1 className="vx-decide-title">{c.title}</h1>
        {c.fields.length > 0 && (
          <dl className="vx-decide-fields" data-preview="">
            {c.fields.map((f) => <div key={f.label} className="vx-decide-field"><dt>{f.label}</dt><dd>{f.value}</dd></div>)}
          </dl>
        )}
        {c.suggestion && <p className="vx-decide-rule" data-rule="">{c.suggestion}</p>}
        {(decisions.length > 0 || elsewhere.length > 0) && (
          <div className="vx-decide-row" data-decisions="">
            {decisions.map((a) => (
              <button key={a.id} type="button" className="vx-decide-act" data-action={a.id} data-tone={a.tone} disabled={busy}
                onClick={() => void decide(c, a)}>{a.label}</button>
            ))}
            {elsewhere.map((a) => <a key={a.id} className="vx-decide-act" data-action={a.id} href={c.href || "/"}>{a.label}</a>)}
          </div>
        )}
        {line && <p className="vx-decide-line" role="status">{line}</p>}
        <p className="vx-decide-foot">{[c.workspace, c.from ? `from ${c.from}` : "", ago(c.at)].filter(Boolean).join(" · ")}</p>
      </article>
      <NotYou email={me.email} onSignOut={signOut} />
    </main>
  );
}

function NotYou({ email, onSignOut }: { email?: string; onSignOut: () => void }) {
  return (
    <p className="vx-decide-notyou">
      {email ? <>Signed in as {email}. </> : null}Not you? <button type="button" className="vx-decide-linkbtn" onClick={onSignOut}>Sign out</button>
    </p>
  );
}

function SignIn({ cardId, carried }: { cardId: string; carried: string }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = email.trim();
    if (!v || sending) return;
    setSending(true);
    try {
      await fetch("/api/auth/request-link", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: v, next: decidePath(cardId) }),
      });
    } catch { /* the answer is the same either way */ }
    setSending(false);
    setSent(true);
  };
  if (carried) return (
    <section className="vx-decide-signin">
      <h1 className="vx-decide-title">Sign in to open this card</h1>
      <p className="vx-decide-say">Your link signs you in once, when you press Continue.</p>
      <a className="vx-decide-act vx-decide-primary" data-continue="" href={redeemHref(carried, cardId)}>Continue</a>
    </section>
  );
  if (sent) return (
    <section className="vx-decide-signin" data-sent="">
      <h1 className="vx-decide-title">Check your email</h1>
      <p className="vx-decide-say">If this address can sign in here, a link is on its way. It brings you back to this card.</p>
    </section>
  );
  return (
    <section className="vx-decide-signin">
      <h1 className="vx-decide-title">Sign in to open this card</h1>
      <form className="vx-decide-form" onSubmit={submit}>
        <label className="vx-decide-label" htmlFor="vx-decide-email">Email</label>
        <input id="vx-decide-email" className="vx-decide-input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <button type="submit" className="vx-decide-act vx-decide-primary" disabled={sending}>Email me a sign-in link</button>
      </form>
    </section>
  );
}
