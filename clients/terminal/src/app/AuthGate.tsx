"use client";
/** Login gate. Polls /api/auth/me on mount; if unauthenticated, renders the sign-in card.
 *
 *  Two real doors, and no third:
 *   • OAuth — Google / Microsoft (next-auth/react `signIn`, which works without a SessionProvider).
 *     Enabled providers are discovered from NextAuth's /api/auth/providers, so a deploy with no
 *     OAuth creds simply hides the buttons.
 *   • EMAIL MAGIC LINK — the address goes to /api/auth/request-link, which mails a signed,
 *     single-use link; clicking it hits /api/auth/redeem, which sets the session cookies and
 *     drops the visitor exactly where they were headed. The card's job here ends at
 *     "Check your email" — this component never mints a session itself.
 *
 *  What used to be here and is now GONE: a `?as=<recipient>` query parameter that POSTed straight
 *  to /api/auth/login, and a "debug sign-in" form onto the same route. Both were password-less —
 *  anyone who could type a URL could become anyone. The emailed link replaces them; the mailbox is
 *  the proof. /api/auth/login still exists for local dev tooling and is refused in production.
 *
 *  FIRST RUN: /api/auth/instance says whether an admin exists. On a fresh instance the card becomes
 *  the one-time "Set up your instance" claim screen. It asks first for the ADMIN CLAIM CODE that
 *  admin-api wrote to its log at boot (/api/auth/claim-code keeps a valid one for the sign-in that
 *  follows), then offers whichever doors the deploy has; that sign-in becomes the admin. No code, no
 *  claim: the first visitor to an exposed instance is not its owner. An existing account can still
 *  sign in plainly from the same screen.
 *
 *  WHO MAY SIGN IN is decided by the server, never here (Vexa-ai/vexa#1783): an existing user, an
 *  admin, or an address on the instance's allow-list. The card learns of a refusal only as an OAuth
 *  round-trip coming back with `?error=` (`takeSigninError`), and shows the one shared sentence. The
 *  email form says "check your email" for every address, allowed or not, by design.
 *
 *  NO COMPANY-LAYER GATE (founder ruling 2026-10-08: "let's remove global setup at all so that
 *  there is no need to setup global at all - let it be empty with no data - it's fine"). This
 *  reverses the 2026-09-02 ruling that a fresh instance served nobody until its admin had written
 *  `_global`. Every signed-in person gets the terminal from their first sign-in, and `_global` may
 *  stay empty for as long as nobody chooses to write it.
 *
 *  What is still decided here, on every page load, is whether the instance HAS an administrator
 *  (`gateVerdict` below): a session minted before any admin existed never walks through a sign-in
 *  door again, so the claim has to be offered to it in place (observed live 2026-09-02, 08:48Z). It
 *  gates `children`, not a banner over them, and it holds a blank screen until the probe has
 *  settled, because rendering the workbench "for now" and retracting it a moment later is the same
 *  defect with a shorter duration. An unreachable probe reads as "an admin exists" and the terminal
 *  renders — a probe that cannot answer never produces a screen that refuses people.
 *
 *  A SESSION THAT DIES MID-USE (2026-09-01). The mount probe used to be the only probe there was:
 *  once this gate said "in" it never asked again, so a session revoked server-side left the entire
 *  shell rendered over an app where every request 401'd — and the user's only report of it was a
 *  chat turn ending in a generic "something went wrong". The gate now LISTENS: the HTTP chokepoints
 *  raise a session-suspect signal on any 401/403 (see @/app/session), this component re-probes
 *  /api/auth/me — which really validates the token now — and only a genuine 401 takes the screen.
 *  The probe is the authority precisely because a 403 is usually resource-scoped and means nothing
 *  about the session; suspicion never signs anybody out on its own. */
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { signIn } from "next-auth/react";
import { onSessionSuspect } from "./session";
import { signinErrorMessage } from "./signinRefusal";
import { SESSION_ENDED_HEADLINE } from "../surfaces/apiClient";

type Status = "checking" | "out" | "in";
type Providers = { google: boolean; microsoft: boolean };

/** Where the link should land: whatever deeplink the visitor already had in the URL (`?ask=`,
 *  `?meeting=`, `?view=`) travels through the mail, so the click is door AND destination. */
function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return window.location.pathname + window.location.search;
}

/** A refused OAuth sign-in comes back to `/?error=<code>` (Vexa-ai/vexa#1783 — see
 *  api/auth/[...nextauth]/authOptions.ts). Read the code ONCE and take it out of the address bar, so
 *  a reload does not repeat a refusal that is over and the next sign-in link does not carry it into
 *  its `next=`. */
export function takeSigninError(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const code = url.searchParams.get("error");
  if (!code) return null;
  url.searchParams.delete("error");
  try { window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash); } catch { /* history unavailable */ }
  return signinErrorMessage(code);
}

/** Don't re-probe more than once every few seconds: a dead session makes EVERY in-flight surface
 *  401 at once, and one answer settles all of them. */
const REPROBE_COOLDOWN_MS = 3000;

/** What a signed-in subject gets. */
export type GateVerdict = "pending" | "open" | "claim";

/** The decision, as a pure function so it is testable without a DOM. There is no company-layer row
 *  any more (founder ruling 2026-10-08): an instance with an administrator is open to everybody who
 *  signed in, and one without offers the claim. */
export function gateVerdict(input: {
  /** The instance probe has settled (answered or failed). Until then: no screen at all. */
  probed: boolean;
  adminExists: boolean;
}): GateVerdict {
  if (!input.probed) return "pending";
  // No admin yet: this is NOT a refusal. Somebody has to be able to claim the instance, and for a
  // session that predates any admin the signed-in person is the only one who can — the sign-in
  // doors they would otherwise claim through are behind them.
  return input.adminExists ? "open" : "claim";
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [providers, setProviders] = useState<Providers>({ google: false, microsoft: false });
  const [adminExists, setAdminExists] = useState(true); // fail-safe: plain sign-in until told otherwise
  // Has the instance probe SETTLED (answered or failed)? Distinct from its value, because "we have
  // not asked yet" and "we asked and an admin exists" must not render the same thing.
  const [instanceProbed, setInstanceProbed] = useState(false);
  const [subjectEmail, setSubjectEmail] = useState<string | null>(null);
  // The claim screen's first step: the one-time code from the admin-api log, accepted by the server.
  const [claimCode, setClaimCode] = useState("");
  const [codeAccepted, setCodeAccepted] = useState(false);
  const [plainSignIn, setPlainSignIn] = useState(false);
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Why the last OAuth round-trip did not sign anybody in, when it said (`?error=` on the way back).
  const [notice, setNotice] = useState<string | null>(null);
  // The session died while the app was open (as opposed to arriving signed-out). Drives the
  // "your session ended" card, whose one button reveals the sign-in card below it.
  const [ended, setEnded] = useState(false);
  // Where the user WAS when it died, captured at that moment so the emailed link / OAuth callback
  // brings them back to the same deeplink.
  const [returnTo, setReturnTo] = useState<string | null>(null);
  const probing = useRef(false);
  const lastProbe = useRef(0);

  /** Confirm a suspicion. Only a real 401 from /api/auth/me takes the screen — a 403 on one
   *  resource, or an unreachable oracle, leaves the session alone. */
  const confirmSession = useCallback(async () => {
    if (probing.current) return;
    const now = Date.now();
    if (now - lastProbe.current < REPROBE_COOLDOWN_MS) return;
    probing.current = true;
    lastProbe.current = now;
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store" });
      if (r.status === 401) {
        setReturnTo((prev) => prev ?? currentPath());
        setEnded(true);
        setStatus("out");
      }
    } catch {
      /* the probe itself couldn't run — that's a network fault, not a dead session */
    } finally {
      probing.current = false;
    }
  }, []);

  useEffect(() => onSessionSuspect(() => { void confirmSession(); }), [confirmSession]);

  useEffect(() => {
    let active = true;
    setNotice(takeSigninError());
    // The session probe also carries what to call the subject on the claim screen.
    fetch("/api/auth/me", { cache: "no-store" })
      .then(async (r) => {
        const body = r.ok ? ((await r.json().catch(() => ({}))) as { user?: { email?: string | null } }) : {};
        if (!active) return;
        setStatus(r.ok ? "in" : "out");
        setSubjectEmail(body.user?.email ?? null);
      })
      .catch(() => active && setStatus("out"));
    // NextAuth lists configured providers here; absent/failed → just no OAuth buttons.
    fetch("/api/auth/providers", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((p: Record<string, unknown>) =>
        active && setProviders({ google: !!p.google, microsoft: !!p.microsoft }))
      .catch(() => undefined);
    // First-run probe — {admin_exists:false} flips the card into the admin-claim variant. It
    // defaults to "an admin exists" on any failure (a bad response, a parse error, an unreachable
    // server), so a probe that cannot answer never shows a claim screen that cannot succeed.
    fetch("/api/auth/instance", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { admin_exists: true }))
      .then((d: { admin_exists?: boolean }) => {
        if (!active) return;
        setAdminExists(d.admin_exists !== false);
        setInstanceProbed(true);
      })
      // A probe that could not run has still SETTLED — it settled on the fail-safe values already in
      // state. Not marking it settled would hold the blank screen forever on an unreachable server,
      // which is the lockout this whole gate is written to avoid.
      .catch(() => active && setInstanceProbed(true));
    return () => { active = false; };
  }, []);

  /** Where the door should put them: the deeplink they were on when the session died, else where
   *  they are now. Captured rather than re-read so a dead session returns to the SAME place. */
  const destination = () => returnTo ?? currentPath();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = email.trim();
    if (!value || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/request-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: value, next: destination() }),
      });
      if (r.ok) { setSent(value); return; }
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      setError(body.error || `Could not send the link (${r.status})`);
    } catch (err) {
      setError((err as Error).message || "Could not send the link");
    } finally {
      setSubmitting(false);
    }
  };

  /** The claim screen's first step: hand the code to the server, which keeps a valid one (httpOnly)
   *  for the sign-in that follows. A wrong one is said so here, at once. */
  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    const value = claimCode.trim();
    if (!value || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/claim-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: value }),
      });
      if (r.ok) { setCodeAccepted(true); return; }
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      setError(body.error || `Could not check the code (${r.status})`);
    } catch (err) {
      setError((err as Error).message || "Could not check the code");
    } finally {
      setSubmitting(false);
    }
  };

  /** Sign out and reload — same discipline as the workbench's own profile row: wipe client state so
   *  the next person does not inherit this one's chats, tabs and pane widths. */
  const signOut = () => {
    void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
      window.location.reload();
    });
  };

  if (status === "in") {
    // Evaluated on every page load — see the header. It is here, above `children`, because the
    // workbench mounts chats and fires dispatches on mount.
    const verdict = gateVerdict({ probed: instanceProbed, adminExists });
    if (verdict === "pending") return <div style={{ height: "100vh", background: "var(--bg)" }} />;
    if (verdict === "claim") return <ClaimInstanceCard email={subjectEmail} onSignOut={signOut} />;
    return <>{children}</>;
  }
  if (status === "checking") return <div style={{ height: "100vh", background: "var(--bg)" }} />;

  // The session died under a running app. Say THAT — not a status code, and not a console pointer —
  // and offer exactly one thing to do about it. The button reveals the sign-in card below, which
  // carries `destination()` so the round trip lands back on the same deeplink.
  if (ended) {
    return (
      <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div
          data-testid="session-ended"
          style={{
            width: 340, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 12,
            padding: 24, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/vexa-logo.svg" alt="Vexa" width={28} height={28} style={{ borderRadius: 8, display: "block", flex: "none" }} />
            <div style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)" }}>{SESSION_ENDED_HEADLINE}</div>
          </div>
          <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
            This device was signed out. Signing in again brings you back to where you were.
          </div>
          <button
            onClick={() => setEnded(false)}
            style={{
              background: "var(--accent)", color: "var(--on-accent)", border: "none", borderRadius: 7,
              padding: "9px 10px", fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}
          >
            Sign in again
          </button>
        </div>
      </div>
    );
  }

  const claiming = !adminExists && !plainSignIn; // fresh instance → this sign-in claims the admin role
  const needCode = claiming && !codeAccepted;     // …once the claim code has been accepted
  // With no OAuth configured (this deploy's /api/auth/providers is empty) the emailed link is not
  // an alternative to anything — it is the door. "Or …" would read as if a button were missing.
  const hasOAuth = providers.google || providers.microsoft;

  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          width: claiming ? 380 : 320, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 12,
          padding: 24, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/vexa-logo.svg" alt="Vexa" width={28} height={28} style={{ borderRadius: 8, display: "block", flex: "none" }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)" }}>
            {claiming ? "Set up your instance" : "Vexa Terminal"}
          </div>
        </div>

        {sent ? (
          <>
            <div style={{ fontSize: 13, color: "var(--t1)", lineHeight: 1.5 }}>Check your email.</div>
            <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 }}>
              If {sent} can sign in here, a link is on its way. It works once and expires in a few minutes.
            </div>
            <button
              onClick={() => { setSent(null); setError(null); }}
              style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0, alignSelf: "flex-start" }}
            >
              Use a different address
            </button>
          </>
        ) : (
          <>
            {notice && (
              <div role="alert" data-testid="signin-notice" style={{ fontSize: 12, color: "var(--danger)", lineHeight: 1.5 }}>
                {notice}
              </div>
            )}
            {needCode ? (
              <form onSubmit={submitCode} data-testid="claim-code" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
                  This Vexa instance has no administrator yet. To claim it, enter the one-time claim code
                  from the admin-api log; whoever signs in with it becomes the admin and can configure
                  models, transcription, and other users.
                </div>
                <input
                  type="text"
                  required
                  autoComplete="one-time-code"
                  spellCheck={false}
                  value={claimCode}
                  onChange={(e) => setClaimCode(e.target.value)}
                  placeholder="XXXX-XXXX-XXXX-XXXX"
                  style={{
                    background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 7,
                    padding: "9px 10px", color: "var(--t1)", fontSize: 13, outline: "none", fontFamily: "var(--mono, monospace)",
                  }}
                />
                {error && <div style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.4 }}>{error}</div>}
                <button
                  type="submit"
                  disabled={!claimCode.trim() || submitting}
                  style={{
                    background: claimCode.trim() ? "var(--accent)" : "var(--panel2)",
                    color: claimCode.trim() ? "var(--on-accent)" : "var(--t3)",
                    border: "none", borderRadius: 7, padding: "9px 10px", fontSize: 13, fontWeight: 600,
                    cursor: claimCode.trim() && !submitting ? "pointer" : "default",
                  }}
                >
                  {submitting ? "Checking…" : "Continue"}
                </button>
                <button type="button" onClick={() => { setPlainSignIn(true); setError(null); }} style={gateQuietBtn}>
                  Already have an account here? Sign in
                </button>
              </form>
            ) : claiming ? (
              <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
                Code accepted. Sign in now — this sign-in becomes the administrator. If you use the
                emailed link, open it in this browser.
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>Sign in to continue.</div>
            )}

            {!needCode && providers.google && (
              <button onClick={() => signIn("google", { callbackUrl: destination() })} style={oauthBtn}>
                <GoogleMark /> Continue with Google
              </button>
            )}
            {!needCode && providers.microsoft && (
              <button onClick={() => signIn("microsoft", { callbackUrl: destination() })} style={oauthBtn}>
                <MicrosoftMark /> Continue with Microsoft
              </button>
            )}

            {!needCode && <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.4 }}>
                {hasOAuth
                  ? "Or get a sign-in link by email."
                  : "Enter your email and we\u2019ll send you a sign-in link."}
              </div>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                style={{
                  background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 7,
                  padding: "9px 10px", color: "var(--t1)", fontSize: 13, outline: "none",
                }}
              />
              {error && <div style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.4 }}>{error}</div>}
              <button
                type="submit"
                disabled={!email.trim() || submitting}
                style={{
                  background: email.trim() ? "var(--accent)" : "var(--panel2)",
                  color: email.trim() ? "var(--on-accent)" : "var(--t3)",
                  border: "none", borderRadius: 7, padding: "9px 10px", fontSize: 13, fontWeight: 600,
                  cursor: email.trim() && !submitting ? "pointer" : "default",
                }}
              >
                {submitting ? "Sending…" : "Send me a link"}
              </button>
            </form>}
          </>
        )}

        {claiming && !sent && (
          <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.4 }}>
            This claim screen disappears once an admin exists.
          </div>
        )}
      </div>
    </div>
  );
}

/** The shell the claim screen sits in — deliberately the same furniture as the sign-in card, so a
 *  person who lands on it recognises where they are. */
function GateShell({ testId, title, children }: { testId: string; title: string; children: React.ReactNode }) {
  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", overflowY: "auto" }}>
      <div
        data-testid={testId}
        style={{
          width: 400, maxWidth: "94vw", background: "var(--panel)", border: "1px solid var(--line2)",
          borderRadius: 12, padding: 24, display: "flex", flexDirection: "column", gap: 14,
          boxShadow: "0 8px 32px rgba(0,0,0,.3)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/vexa-logo.svg" alt="Vexa" width={28} height={28} style={{ borderRadius: 8, display: "block", flex: "none" }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)" }}>{title}</div>
        </div>
        {children}
      </div>
    </div>
  );
}

const gateQuietBtn: React.CSSProperties = {
  background: "none", border: "none", color: "var(--t3)", fontSize: 11.5,
  cursor: "pointer", padding: 0, alignSelf: "flex-start", textDecoration: "underline",
};

/** The instance has no administrator and this person is signed in.
 *
 *  This is NOT a refusal and must not read like one. It is also not a dismissible notice: claiming
 *  is the single highest-privilege act the product offers and it cannot be undone from inside the
 *  product (there is no second administrator to reverse it). So the button comes AFTER the sentence
 *  that says what it means, not before it. */
function ClaimInstanceCard({ email, onSignOut }: { email: string | null; onSignOut: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");

  const claim = async () => {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/claim-admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.trim() }),
      });
      if (r.ok) {
        // FOLLOW THE URL THE SERVER HANDED BACK — the claimer's first-visit arrival (`/?s=<id>`), or
        // `/` when there is none. A full navigation, not a flip: the claim changes what every probe
        // on this page would answer, so nothing may be left holding the old answer.
        const body = (await r.json().catch(() => ({}))) as { url?: string };
        window.location.assign(body.url || "/");
        return;
      }
      const body = (await r.json().catch(() => ({}))) as { error?: string; reload?: boolean };
      // Somebody else claimed it first — the screen is stale, not broken. Reloading shows the truth.
      if (body.reload) { window.location.reload(); return; }
      setError(body.error || `Could not claim this instance (${r.status})`);
    } catch (err) {
      setError((err as Error).message || "Could not claim this instance");
    } finally {
      setBusy(false);
    }
  };

  return (
    <GateShell testId="claim-instance" title="Set up this Vexa">
      <div style={{ fontSize: 12.5, color: "var(--t1)", lineHeight: 1.55 }}>
        This Vexa has no administrator yet.
      </div>
      <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.6 }}>
        Claiming it makes {email ? <strong style={{ color: "var(--t2)", fontWeight: 600 }}>{email}</strong> : "you"} this
        instance&rsquo;s administrator, who configures models, transcription, and other users.
      </div>
      <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 }}>
        There is no second administrator to undo this, so claim it only if the instance is yours to run.
        Claiming takes the one-time claim code from the admin-api log.
      </div>
      <input
        type="text"
        autoComplete="one-time-code"
        spellCheck={false}
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="XXXX-XXXX-XXXX-XXXX"
        aria-label="Claim code"
        style={{
          background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 7,
          padding: "9px 10px", color: "var(--t1)", fontSize: 13, outline: "none", fontFamily: "var(--mono, monospace)",
        }}
      />
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.45 }}>{error}</div>}
      <button
        onClick={() => void claim()}
        disabled={busy || !code.trim()}
        style={{
          background: "var(--accent)", color: "var(--on-accent)", border: "none", borderRadius: 7,
          padding: "10px 12px", fontSize: 13, fontWeight: 600, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
        }}
      >
        {busy ? "Claiming\u2026" : "Claim this instance"}
      </button>
      <button onClick={onSignOut} style={gateQuietBtn}>Not you? Sign out</button>
    </GateShell>
  );
}

const oauthBtn: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
  background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)",
  borderRadius: 7, padding: "10px 10px", fontSize: 13, fontWeight: 600, cursor: "pointer",
};

/** Google's multicolor "G" brand mark. */
function GoogleMark() {
  return (
    <svg width={18} height={18} viewBox="0 0 48 48" style={{ flex: "none" }} aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

/** Microsoft's four-square brand mark. */
function MicrosoftMark() {
  return (
    <svg width={16} height={16} viewBox="0 0 23 23" style={{ flex: "none" }} aria-hidden="true">
      <path fill="#F25022" d="M1 1h10v10H1z" />
      <path fill="#7FBA00" d="M12 1h10v10H12z" />
      <path fill="#00A4EF" d="M1 12h10v10H1z" />
      <path fill="#FFB900" d="M12 12h10v10H12z" />
    </svg>
  );
}
