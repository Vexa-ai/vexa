"use client";
/** `/join?i=<token>` — where an invite is redeemed.
 *
 *  **The founder minted an invite link, opened it, and read *"not found"*** (Vexa-ai/vexa#1635).
 *  Two things were wrong and this file is the second of them: the link pointed at the MCP host
 *  (fixed where the link is composed — agent-api builds it on the deployment's declared public app
 *  URL now), and *nothing served `/join` on the terminal at all*. A token is not an invitation
 *  until there is a page that turns it into one.
 *
 *  The order is the point, and it is: **say what this is, THEN ask who they are.**
 *
 *    1. PREVIEW, before any sign-in. `GET /api/join/preview?i=` is capability-gated by the token
 *       itself — whoever holds the link may see what they are being invited to — so the card can
 *       say *"Dmitry invited you to Example Bank as a contributor: you can read and write its pages"*
 *       to somebody who has no account here yet. Asking a stranger to sign in to find out what
 *       they are signing in FOR is how an invite reads as a phish.
 *    2. SIGN IN, with this instance's own door — the emailed link and whatever OAuth the deploy
 *       has, unchanged, carrying `next=/join?i=…` so one click is door AND destination and the
 *       browser comes back here holding a session. When the invite is bound to ONE address that
 *       address is prefilled and locked: it is the only one the redeem will accept, and letting
 *       somebody type another is offering them a door that is going to close in their face.
 *    3. CONFIRM, then REDEEM (PRD 0.13.4 D26). A visitor who is already signed in sees the
 *       workspace, the role and the sender, and joins only when they press Join — never on load.
 *       The card says who the browser is signed in as, with "Not you? Use another address" (D35:
 *       one person, many addresses), which signs this browser out and returns to the sign-in door.
 *       `POST /api/workspace/invites/accept` — the route that already existed. It is the
 *       authenticated edge, so it sees the gateway's verified email and enforces the binding
 *       itself; nothing here is a security check, it is the same answer rendered early.
 *    4. LAND on the workspace's front page, `/w/<id>` — resolved from the slug `accept` returns,
 *       and on `/w/<slug>` when that resolution failed: the route takes either (#1643), so a join
 *       lands on the workspace it joined rather than on the terminal's front door.
 *
 *  A NEW PERSON TAKES THE SAME PATH. The magic-link door creates the account on redeem exactly as
 *  it does for any other destination, and the first-visit arrival keeps the rule it already has:
 *  a link that names a destination keeps it, and this link names one. Nothing about arrival is
 *  special-cased here, which is why nothing about it can drift.
 *
 *  ⚠ AND NEVER A 404. Expired, spent, withdrawn, unknown, or bound to another address — five
 *  outcomes, one sentence each, from `refusal()` in ./joinState. The page the founder opened
 *  answered with a web server's "not found", which tells a person nothing about the invite in
 *  their hand and reads as "this product is broken".
 *
 *  This route deliberately does NOT render `<App/>`: the workbench mounts chats and fires
 *  dispatches behind AuthGate, and the whole point of this page is that it renders for somebody
 *  who is not signed in yet.
 *
 *  ⚠ IT IS NOT THE ONLY REDEEMER, AND THAT IS A KNOWN SEAM. `InviteGate` in `../App.tsx` redeems
 *  `?invite=` links minted by the terminal's own in-app share controls, and it lives INSIDE the
 *  signed-in shell — which is why it cannot serve the case this page exists for: a person with no
 *  account here, who must be told what they are joining before being asked to sign in. Every link
 *  an AGENT hands out now points here (`workspace_membership.invite_link`, one composer). Folding
 *  the in-app controls onto this page is the obvious next move and it is a decision, not a
 *  refactor, so it is named on Vexa-ai/vexa#1635 rather than taken here.
 */
import { Identity } from "../../ui-kit/Identity";
import "./join.css";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { signIn } from "next-auth/react";
import { providersFrom } from "../AuthGate";
import {
  boundAddress,
  inviteSentence,
  maskAddress,
  NOT_YOU,
  notYouHelps,
  oneClickNote,
  refusal,
  refusalForAcceptStatus,
  refusalForPreviewStatus,
  refusalForReason,
  returnPath,
  roomOrWorkspacePath,
  signedInLine,
  type InvitePreview,
  type RefusalKind,
} from "./joinState";

type Phase = "loading" | "preview" | "confirm" | "refused" | "joining" | "joined";
type Providers = { google: boolean; microsoft: boolean; oidc: string | null };

/** The token out of `?i=`. Read from `window.location` rather than `useSearchParams` so this page
 *  needs no Suspense boundary — the same call `/w/[...ref]` makes for the same reason.
 *
 *  `?invite=` is accepted as an alias, and only here: that is the spelling the terminal's OWN
 *  in-app share controls have always minted (`workspaceManage`, `meetingPrep`, `roomOnboarding` →
 *  `<origin>/?invite=`, redeemed by `InviteGate` INSIDE the signed-in shell). Those keep working
 *  where they are; this alias only means that a `/join` url carrying the older parameter name
 *  redeems instead of reading as a link with no invite in it. */
function tokenFromUrl(): string {
  if (typeof window === "undefined") return "";
  const q = new URLSearchParams(window.location.search);
  return q.get("i") || q.get("invite") || "";
}

/** Why the one-click came back to this page (`n=`, set by `POST /api/join/accept`), or "". */
function noteFromUrl(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("n") || "";
}

/** ‹ Back (PRD 0.13.4 §11, phone): the previous page when there is one in this tab — the mail app
 *  usually opened us in a fresh one — else the app's front door. Never leaves a person stuck. */
function goBack() {
  if (typeof window === "undefined") return;
  if (window.history.length > 1) window.history.back();
  else window.location.assign("/");
}

export default function JoinPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [why, setWhy] = useState<RefusalKind>("unknown");
  const [token, setToken] = useState("");
  const [note, setNote] = useState("");
  const [signedIn, setSignedIn] = useState(false);
  const [signedInAs, setSignedInAs] = useState("");
  const [providers, setProviders] = useState<Providers>({ google: false, microsoft: false, oidc: null });
  // The emailed link is drawn unless the instance says the operator closed it (VEXA_SIGNIN_METHODS).
  const [emailLink, setEmailLink] = useState(true);
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A redeem must happen once. Both probes settle independently and React may re-run the effect;
  // a second POST is harmless server-side (accept is idempotent per user) and confusing here.
  const redeemed = useRef(false);

  const refuse = useCallback((kind: RefusalKind) => {
    setWhy(kind);
    setPhase("refused");
  }, []);

  /** Redeem, then land. The workspace id comes from the registry via the slug `accept` returned —
   *  a resolution that can fail without the join failing, because by then they ARE a member. */
  const redeem = useCallback(async (tok: string) => {
    if (redeemed.current) return;
    redeemed.current = true;
    setPhase("joining");
    let slug = "";
    let room: { id?: string } | null = null;
    try {
      const r = await fetch("/api/workspace/invites/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tok }),
      });
      if (!r.ok) {
        redeemed.current = false;
        refuse(refusalForAcceptStatus(r.status));
        return;
      }
      const body = (await r.json().catch(() => ({}))) as { workspace_id?: string; room?: { id?: string } | null };
      slug = body.workspace_id || "";
      room = body.room && body.room.id ? body.room : null;
    } catch {
      redeemed.current = false;
      refuse("unreachable");
      return;
    }
    let id = "";
    try {
      const rec = await fetch(`/api/workspaces/by-slug/${encodeURIComponent(slug)}`, { cache: "no-store" });
      if (rec.ok) id = ((await rec.json().catch(() => ({}))) as { id?: string }).id || "";
    } catch {
      /* the id is a nicety; membership is the thing that happened */
    }
    setPhase("joined");
    // The slug is what `accept` answered with and it always resolved; the id is the nicety that may
    // not have. Either one addresses the workspace's front page through the one `/w/…` route.
    // The room the invite named, when there is one (PRD 0.13.4 S2); else the workspace.
    window.location.assign(roomOrWorkspacePath(id, room, slug));
  }, [refuse]);

  useEffect(() => {
    const tok = tokenFromUrl();
    setToken(tok);
    setNote(noteFromUrl());
    if (!tok) { refuse("no-token"); return; }
    let active = true;

    // NextAuth's configured providers — absent/failed is just no OAuth buttons, exactly as AuthGate
    // treats it. A deploy with no OAuth creds has the emailed link and that is a complete door.
    fetch("/api/auth/providers", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((p: Record<string, unknown>) => active && setProviders(providersFrom(p)))
      .catch(() => undefined);
    fetch("/api/auth/instance", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((d: { email_link?: boolean }) => active && setEmailLink(d.email_link !== false))
      .catch(() => undefined);

    void (async () => {
      // WHO, and WHAT — asked together, because the page's whole shape depends on both.
      const [meRes, prevRes] = await Promise.all([
        fetch("/api/auth/me", { cache: "no-store" }).catch(() => null),
        fetch(`/api/join/preview?i=${encodeURIComponent(tok)}`, { cache: "no-store" }).catch(() => null),
      ]);
      if (!active) return;

      if (!prevRes) { refuse("unreachable"); return; }
      if (!prevRes.ok) { refuse(refusalForPreviewStatus(prevRes.status)); return; }
      const p = (await prevRes.json().catch(() => null)) as InvitePreview | null;
      if (!active) return;
      if (!p) { refuse("unreachable"); return; }
      setPreview(p);
      if (!p.valid) { refuse(refusalForReason(p.reason)); return; }

      const inSession = !!meRes?.ok;
      setSignedIn(inSession);
      if (inSession) {
        const me = (await meRes!.json().catch(() => ({}))) as { user?: { email?: string | null } };
        if (!active) return;
        setSignedInAs(String(me.user?.email || ""));
      }
      // A bound invite fills the field with the address it admits — the visitor is not asked to
      // remember which of their addresses a colleague typed.
      const bound = boundAddress(p);
      if (bound) setEmail(bound);
      // A SIGNED-IN VISITOR CONFIRMS (PRD 0.13.4 D26, security review H4), and sees WHO is signed
      // in, so the wrong one of their addresses never joins by accident (D35, "Not you?").
      // Joining never changes where the person's chats write — the server no longer queues a profile.
      if (inSession) { setPhase("confirm"); return; }
      setPhase("preview");
    })();

    return () => { active = false; };
  }, [refuse, redeem]);

  /** "Not you? Use another address": sign this browser out (the session and the client state, as
   *  AuthGate's own sign-out does) and show the sign-in door again on this same invite. Nothing joined. */
  const notYou = async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch { /* signed out or not, the door is shown */ }
    try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
    setSignedIn(false);
    setSignedInAs("");
    setSent(null);
    setError(null);
    if (!boundAddress(preview)) setEmail("");
    setPhase(preview && preview.valid ? "preview" : "refused");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = email.trim();
    if (!value || sending) return;
    setSending(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/request-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: value, next: returnPath(token) }),
      });
      if (r.ok) { setSent(value); return; }
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      setError(body.error || `Could not send the link (${r.status})`);
    } catch (err) {
      setError((err as Error).message || "Could not send the link");
    } finally {
      setSending(false);
    }
  };

  const bound = boundAddress(preview);
  const locked = !!bound;
  // ONE CLICK (PRD 0.13.4 S2): a mailed invite signs its one address in. The button is a plain
  // form POST — no script submits it, so a scanner that renders the page changes nothing.
  const oneClick = phase === "preview" && !!preview?.signin && !!bound && emailLink && !note;
  const where = preview ? ((preview.room?.title ? `${preview.room.title}` : preview.name || preview.workspace_id) || "") : "";

  return (
    <div className="p-4" style={shell}>
      <div style={card} data-testid="join-card">
        <button type="button" onClick={goBack} className="bg-none bd-none c-2 t-sm p-0" style={backBtn}
          data-testid="join-back" aria-label="Back">
          ‹ Back
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/vexa-logo.svg" alt="Vexa" width={28} height={28} className="r-md" style={{ display: "block", flex: "none" }} />
          <div className="t-md fw-600 c-1">
            {phase === "refused" ? "This invite" : "You have been invited"}
          </div>
        </div>

        {phase === "loading" && (
          <div style={muted} data-testid="join-loading">Checking this invite…</div>
        )}

        {phase === "refused" && (
          <div className="t-xs c-2 lh-normal" data-testid="join-refused">
            {refusal(why)}
          </div>
        )}
        {phase === "refused" && notYouHelps(why, signedIn) && (
          <button type="button" onClick={() => { void notYou(); }} className="vx-join-quiet" data-testid="join-not-you">
            {NOT_YOU}
          </button>
        )}

        {phase === "confirm" && preview && (
          <>
            <Identity kind="workspace" id={preview.workspace_id} name={preview.name || preview.workspace_id} size={40} label />
            <div style={sentence} data-testid="join-sentence">{inviteSentence(preview)}</div>
            {preview.purpose ? (
              <div style={muted} data-testid="join-purpose">{preview.purpose}</div>
            ) : null}
            <div style={muted} data-testid="join-confirm-note">
              Joining adds this workspace to your list. Your chats keep writing where they write now.
            </div>
            {signedInLine(signedInAs) && (
              <div style={muted} data-testid="join-signed-in-as">{signedInLine(signedInAs)}</div>
            )}
            <button type="button" onClick={() => void redeem(token)} style={submitBtn} data-testid="join-confirm">
              Join {preview.name || preview.workspace_id}
            </button>
            <button type="button" onClick={() => window.location.assign("/")} className="vx-join-quiet" data-testid="join-not-now">
              Not now
            </button>
            <button type="button" onClick={() => { void notYou(); }} className="vx-join-quiet" data-testid="join-not-you">
              {NOT_YOU}
            </button>
          </>
        )}

        {(phase === "joining" || phase === "joined") && preview && (
          <>
            <Identity kind="workspace" id={preview.workspace_id} name={preview.name || preview.workspace_id} size={40} label />
            <div style={sentence} data-testid="join-sentence">{inviteSentence(preview)}</div>
            <div style={muted} data-testid="join-joining">Adding you to it…</div>
          </>
        )}

        {phase === "preview" && preview && (
          <>
            <Identity kind="workspace" id={preview.workspace_id} name={preview.name || preview.workspace_id} size={40} label />
            <div style={sentence} data-testid="join-sentence">{inviteSentence(preview)}</div>
            {preview.purpose ? (
              <div style={muted} data-testid="join-purpose">{preview.purpose}</div>
            ) : null}

            {note && oneClickNote(note) ? (
              <div style={muted} data-testid="join-note">{oneClickNote(note)}</div>
            ) : null}

            {oneClick && !sent ? (
              <form method="post" action="/api/join/accept" style={{ display: "flex", flexDirection: "column", gap: 8 }}
                data-testid="join-one-click">
                <input type="hidden" name="i" value={token} />
                <button type="submit" style={submitBtn} data-testid="join-one-click-button">
                  Join {where}
                </button>
                <div style={muted}>
                  Signs you in as {maskAddress(bound)} — this link was sent to that address. It works once.
                </div>
              </form>
            ) : sent ? (
              <>
                <div className="t-sm c-1 lh-snug">Check your email.</div>
                <div style={muted}>
                  If {sent} can sign in here, a link is on its way. It brings you straight back to this
                  invite. It works once and expires in a few minutes.
                </div>
                {!locked && (
                  <button
                    onClick={() => { setSent(null); setError(null); }}
                    className="bg-none bd-none c-3 t-xs p-0" style={{ cursor: "pointer", alignSelf: "flex-start" }}
                  >
                    Use a different address
                  </button>
                )}
              </>
            ) : (
              <>
                <div style={muted}>Sign in to accept it.</div>

                {providers.google && (
                  <button onClick={() => signIn("google", { callbackUrl: returnPath(token) })} style={oauthBtn}>
                    Continue with Google
                  </button>
                )}
                {providers.microsoft && (
                  <button onClick={() => signIn("microsoft", { callbackUrl: returnPath(token) })} style={oauthBtn}>
                    Continue with Microsoft
                  </button>
                )}
                {providers.oidc && (
                  <button onClick={() => signIn("oidc", { callbackUrl: returnPath(token) })} style={oauthBtn}>
                    Continue with {providers.oidc}
                  </button>
                )}

                {emailLink && <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="t-xs c-3 lh-snug">
                    {locked
                      ? "This invite is for this address. We’ll email it a sign-in link."
                      : "Enter your email and we’ll send you a sign-in link."}
                  </div>
                  <input
                    type="email"
                    required
                    readOnly={locked}
                    autoComplete="email"
                    data-testid="join-email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    className="bg-0 bd-strong r-md pt-2 pr-2 pb-2 pl-2 t-sm" style={{ color: locked ? "var(--t2)" : "var(--t1)", outline: "none", width: "100%", boxSizing: "border-box", minHeight: 44 }}
                  />
                  <button type="submit" disabled={sending} style={submitBtn}>
                    {sending ? "Sending…" : "Email me a sign-in link"}
                  </button>
                </form>}
                {error && <div className="t-xs c-danger" data-testid="join-error">{error}</div>}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// PHONE (PRD 0.13.4 §11): at 390px the card fills the width inside a 16px gutter, nothing scrolls
// sideways (long addresses and names wrap anywhere), the page scrolls vertically when the keyboard
// is up, and the one act on the page is full width and at least 44px tall.
const shell: React.CSSProperties = {
  minHeight: "100dvh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center",
  boxSizing: "border-box", overflowX: "hidden",
};
const card: React.CSSProperties = {
  width: "100%", maxWidth: 360, boxSizing: "border-box", background: "var(--panel)", border: "1px solid var(--line2)",
  borderRadius: 12, padding: 20, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)",
};
const backBtn: React.CSSProperties = { alignSelf: "flex-start", minHeight: 32, cursor: "pointer" };
const sentence: React.CSSProperties = { fontSize: 13, color: "var(--t1)", lineHeight: 1.55 };
const muted: React.CSSProperties = { fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 };
const oauthBtn: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "center", gap: 8, background: "var(--bg)",
  color: "var(--t1)", border: "1px solid var(--line2)", borderRadius: 7, padding: "11px 12px",
  fontSize: 14, fontWeight: 500, cursor: "pointer", width: "100%", minHeight: 44,
};
const submitBtn: React.CSSProperties = {
  background: "var(--accent)", color: "var(--on-accent)", border: "none", borderRadius: 7,
  padding: "11px 12px", fontSize: 14, fontWeight: 600, cursor: "pointer", width: "100%", minHeight: 44,
};
