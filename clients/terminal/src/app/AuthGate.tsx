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
 *  the one-time "Set up your instance" claim screen — the first sign-in becomes the admin —
 *  through whichever door the deploy actually has.
 *
 *  THE COMPANY-LAYER GATE (founder ruling 2026-09-02: "global needs to be setup by admin, it just
 *  should not let him start the service before that"). The same probe now also reports
 *  `global_setup`. Until the admin has written the company layer into the platform `_global`
 *  workspace, this instance serves NOBODY but that admin — so while the gate is up the card stops
 *  advertising itself as a way in for ordinary people: the ordinary "Sign in to continue" framing
 *  and the one-click provider buttons are replaced by one sentence saying what is happening.
 *
 *  What is deliberately NOT hidden is a door for the ADMIN. Hiding every provider button outright
 *  would brick an OAuth-only deploy the moment the admin's session lapsed mid-setup — the gate would
 *  be locking out the one person it exists to wait for. So the OAuth buttons move behind an explicit
 *  "I'm the administrator" disclosure: an ordinary visitor sees a setup notice and no doors, the
 *  admin is one labelled click from theirs. THIS IS PRESENTATION, NOT ENFORCEMENT — the actual
 *  refusal lives server-side in /api/auth/{login,redeem} and the OAuth signIn callback, all three of
 *  which ask admin-api before any user row can be created. Nothing here is load-bearing for
 *  security; it is load-bearing for not telling ordinary users a lie about what will happen.
 *
 *  ⚠ A GATE ON THE DOORS IS NOT A GATE (observed live 2026-09-02, 08:48Z). The refusals above live
 *  in /api/auth/{login,redeem} and the OAuth callback — all three of which a session minted BEFORE
 *  the gate existed never touches again. On a gated instance a browser holding such a cookie got the
 *  whole terminal and a personal chat with the ordinary greeting; nothing refused it, because
 *  nothing ever re-asked. A door check answers "may this person come in"; the question the gate
 *  actually poses is "may this person BE in", and that has to be answered on every page load.
 *
 *  So this component now decides FOUR rows before it renders `children`, and `setupGateVerdict`
 *  below is that decision as a pure function:
 *
 *    | instance             | subject      | screen                                          |
 *    |----------------------|--------------|-------------------------------------------------|
 *    | layer written        | anyone       | the terminal, unchanged                         |
 *    | missing, has admin   | the admin    | the terminal + the setup wizard (SetupGate)     |
 *    | missing, has admin   | anyone else  | refused — one sentence and a way to sign out    |
 *    | missing, NO admin    | whoever      | the claim screen                                |
 *
 *  Two things about the shape are load-bearing. It gates `children`, not a banner over them: the
 *  workbench MOUNTS CHATS AND FIRES DISPATCHES on mount, and SetupGate (which starts polling)
 *  sits inside this component — a refused user must never reach either. And it holds a blank screen
 *  until both probes have settled, because rendering the workbench "for now" and retracting it a
 *  moment later is the same defect with a shorter duration.
 *
 *  The fail direction does NOT change here: an unreachable probe still reads as "completed" and the
 *  terminal renders. The closed half is server-side — agent-api refuses chat dispatch and workspace
 *  writes for non-admin subjects while the gate is up — so a browser that renders on a blip can
 *  still do nothing.
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
import { setCompanyLayerHint } from "../minutes/chats";
import { COMPANY_LAYER_EVENT } from "../canvas/actions";
import { signIn } from "next-auth/react";
import { onSessionSuspect } from "./session";
import { SESSION_ENDED_HEADLINE } from "../surfaces/apiClient";

type Status = "checking" | "out" | "in";
type Providers = { google: boolean; microsoft: boolean };

/** Where the link should land: whatever deeplink the visitor already had in the URL (`?ask=`,
 *  `?meeting=`, `?view=`) travels through the mail, so the click is door AND destination. */
function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return window.location.pathname + window.location.search;
}

/** Don't re-probe more than once every few seconds: a dead session makes EVERY in-flight surface
 *  401 at once, and one answer settles all of them. */
const REPROBE_COOLDOWN_MS = 3000;

/** The company-layer gate's sentence, character-for-character the SETUP_GATE_REFUSAL that
 *  api/auth/adminApi.ts sends from the login route, the magic-link refusal card and the OAuth
 *  callback. It is duplicated rather than imported because this is a client component and that
 *  module is server-only (it reads VEXA_INTERNAL_API_SECRET at call time); importing it would drag
 *  the internal secret's module graph into the browser bundle. If one of the two ever changes, the
 *  other must change with it — a visitor who gets refused at the door and then reads a differently
 *  worded notice on the screen they land back on has been told two things about one state. */
const SETUP_GATE_NOTICE = "This Vexa is being set up by its administrator.";

/** What a signed-in subject gets while the company-layer gate is up. */
export type GateVerdict = "pending" | "open" | "claim" | "refused";

/** The four-row decision, as a pure function so the table above is testable without a DOM.
 *
 *  `isAdmin` is THREE-VALUED (see /api/auth/me): null means the oracle could not say. Note the
 *  final line — we refuse only on a positive `false`. Unknown reads as "let them in", matching the
 *  direction every other fail-safe in this gate takes, and leaving the actual refusal to agent-api,
 *  which decides with authoritative state and fails closed. */
export function setupGateVerdict(input: {
  /** Both probes have settled (either answered or failed). Until then: no screen at all. */
  probed: boolean;
  globalSetup: "completed" | "missing";
  adminExists: boolean;
  isAdmin: boolean | null;
}): GateVerdict {
  if (!input.probed) return "pending";
  if (input.globalSetup !== "missing") return "open";
  // No admin yet: this is NOT a refusal. Somebody has to be able to claim the instance, and on an
  // instance with live pre-gate sessions the signed-in person is the only one who can — the sign-in
  // doors they would otherwise claim through are behind them.
  if (!input.adminExists) return "claim";
  return input.isAdmin === false ? "refused" : "open";
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [providers, setProviders] = useState<Providers>({ google: false, microsoft: false });
  const [adminExists, setAdminExists] = useState(true); // fail-safe: plain sign-in until told otherwise
  // Fail-safe towards "completed", matching the server's own direction (adminApi.instanceState):
  // an unreachable probe must never present a lockout screen on a healthy instance.
  const [globalSetup, setGlobalSetup] = useState<"completed" | "missing">("completed");
  // Has the instance probe SETTLED (answered or failed)? Distinct from its values, because "we have
  // not asked yet" and "we asked and it said the gate is down" must not render the same thing.
  const [instanceProbed, setInstanceProbed] = useState(false);
  // Is the validated subject this instance's admin? null = the oracle could not say (see /api/auth/me).
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [subjectEmail, setSubjectEmail] = useState<string | null>(null);
  // The admin's escape hatch while the gate is up — reveals the provider buttons on request.
  const [adminDoor, setAdminDoor] = useState(false);
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    // The session probe also carries the two facts the gate needs about the SUBJECT: whether they
    // are the admin, and what to call them on a refusal screen.
    fetch("/api/auth/me", { cache: "no-store" })
      .then(async (r) => {
        const body = r.ok ? ((await r.json().catch(() => ({}))) as { is_admin?: boolean | null; user?: { email?: string | null } }) : {};
        if (!active) return;
        setStatus(r.ok ? "in" : "out");
        setIsAdmin(typeof body.is_admin === "boolean" ? body.is_admin : null);
        setSubjectEmail(body.user?.email ?? null);
      })
      .catch(() => active && setStatus("out"));
    // NextAuth lists configured providers here; absent/failed → just no OAuth buttons.
    fetch("/api/auth/providers", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((p: Record<string, unknown>) =>
        active && setProviders({ google: !!p.google, microsoft: !!p.microsoft }))
      .catch(() => undefined);
    // First-run probe — {admin_exists:false} flips the card into the admin-claim variant, and
    // {global_setup:"missing"} flips it into the setup-in-progress variant. Both default to the
    // permissive reading on any failure (a bad response, a parse error, an unreachable server), so
    // a probe that cannot answer never produces a screen that refuses people.
    fetch("/api/auth/instance", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { admin_exists: true, global_setup: "completed" }))
      .then((d: { admin_exists?: boolean; global_setup?: string }) => {
        if (!active) return;
        setAdminExists(d.admin_exists !== false);
        const layer = d.global_setup === "missing" ? "missing" : "completed";
        setGlobalSetup(layer);
        // THE RAIL'S CACHE OF THIS SAME FIELD, written HERE because this probe is the one that
        // always runs. `loadChats` is synchronous and cannot await anything, so the rail reads a
        // cached answer to decide whether the structural rows exist yet.
        //
        // ⚠ It used to be written only by the setup card's poll — and that card stops rendering the
        // moment setup completes, so the cache froze at whatever it last saw. An admin who finished
        // the company layer and reloaded could keep a stale "missing" forever, and their Personal
        // row would silently never come back. A cache whose only writer is a component that
        // disappears is a cache that goes stale by design.
        //
        // Both writers copy the SERVER's field verbatim and neither computes it, so this is two
        // read points of one source of truth, not two opinions about it. The card keeps its write
        // because it is the one watching for the flip DURING a session, when this probe ran long ago.
        setCompanyLayerHint(layer);
        window.dispatchEvent(new CustomEvent(COMPANY_LAYER_EVENT));
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

  /** Sign out and reload — same discipline as the workbench's own profile row: wipe client state so
   *  the next person does not inherit this one's chats, tabs and pane widths. */
  const signOut = () => {
    void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
      window.location.reload();
    });
  };

  if (status === "in") {
    // THE GATE, evaluated on every page load — see the four-row table in the header. It is here,
    // above `children`, because the workbench mounts chats and fires dispatches on mount and
    // SetupGate (inside it) starts polling: a refused subject must reach neither.
    const verdict = setupGateVerdict({ probed: instanceProbed, globalSetup, adminExists, isAdmin });
    if (verdict === "pending") return <div style={{ height: "100vh", background: "var(--bg)" }} />;
    if (verdict === "claim") return <ClaimInstanceCard email={subjectEmail} onSignOut={signOut} />;
    if (verdict === "refused") return <SetupRefusedCard email={subjectEmail} onSignOut={signOut} />;
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

  const claiming = !adminExists; // fresh instance → this sign-in claims the admin role
  // The company layer has not been written yet: this instance serves nobody but its admin. Note the
  // `!claiming` — on a VIRGIN instance (no admin at all) the gate is not up for anybody, because the
  // next sign-in is the one that becomes the admin; showing "wait for the administrator" to the
  // person who is about to BE the administrator is a deadlock with a polite sentence on it.
  const setupGated = globalSetup === "missing" && !claiming;
  // With no OAuth configured (this deploy's /api/auth/providers is empty) the emailed link is not
  // an alternative to anything — it is the door. "Or …" would read as if a button were missing.
  const hasOAuth = providers.google || providers.microsoft;
  // Ordinary visitors get no provider buttons while the gate is up; the admin reveals them.
  const showProviders = !setupGated || adminDoor;

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
            {claiming ? "Set up your instance" : setupGated ? "Setting up this Vexa" : "Vexa Terminal"}
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
            {claiming ? (
              <>
                <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
                  This Vexa instance has no administrator yet. The first sign-in becomes the admin and can
                  configure models, transcription, and other users.
                </div>
                <div
                  style={{
                    alignSelf: "flex-start", fontSize: 11, color: "var(--t2)", border: "1px solid var(--line2)",
                    borderRadius: 20, padding: "3px 10px", display: "inline-flex", alignItems: "center", gap: 6,
                  }}
                >
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--accent)", display: "inline-block" }} />
                  First sign-in = administrator
                </div>
              </>
            ) : setupGated ? (
              <div style={{ fontSize: 12, color: "var(--t2)", lineHeight: 1.55 }} data-testid="setup-gate-notice">
                {SETUP_GATE_NOTICE}
                <div style={{ color: "var(--t3)", marginTop: 6 }}>
                  Until that is finished, only the administrator can sign in.
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>Sign in to continue.</div>
            )}

            {/* While the gate is up the provider buttons are not offered to ordinary visitors — see
                the header comment. They stay reachable for the admin behind the disclosure below. */}
            {showProviders && providers.google && (
              <button onClick={() => signIn("google", { callbackUrl: destination() })} style={oauthBtn}>
                <GoogleMark /> Continue with Google
              </button>
            )}
            {showProviders && providers.microsoft && (
              <button onClick={() => signIn("microsoft", { callbackUrl: destination() })} style={oauthBtn}>
                <MicrosoftMark /> Continue with Microsoft
              </button>
            )}

            <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.4 }}>
                {setupGated
                  ? "Administrator: enter your email and we\u2019ll send you a sign-in link."
                  : hasOAuth
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
            </form>
          </>
        )}

        {/* The admin's escape hatch. An OAuth-only deploy whose admin session lapses mid-setup would
            otherwise have NO visible door at all — the gate would lock out the one person it is
            waiting for. One labelled click, not a button ordinary visitors are invited to press. */}
        {setupGated && !sent && hasOAuth && !adminDoor && (
          <button
            onClick={() => setAdminDoor(true)}
            style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0, alignSelf: "flex-start", textDecoration: "underline" }}
          >
            I&rsquo;m the administrator
          </button>
        )}

        {claiming && !sent && (
          <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.4 }}>
            This claim screen disappears once an admin exists.
          </div>
        )}

        {setupGated && !sent && (
          <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.4 }}>
            This notice disappears as soon as the company layer is written.
          </div>
        )}
      </div>
    </div>
  );
}

/** The shell both gate screens sit in — deliberately the same furniture as the sign-in card, so a
 *  person who lands on one of these recognises where they are. */
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

/** ROW 4 — the instance has no administrator and this person is signed in.
 *
 *  This is NOT a refusal and must not read like one. It is also not a dismissible notice: claiming
 *  is the single highest-privilege act the product offers, it cannot be undone from inside the
 *  product (there is no second administrator to reverse it), and the person pressing the button is
 *  taking on writing the company layer that every agent in the company then carries. So the button
 *  comes AFTER the sentence that says what it means, not before it. */
function ClaimInstanceCard({ email, onSignOut }: { email: string | null; onSignOut: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const claim = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/claim-admin", { method: "POST" });
      if (r.ok) {
        // FOLLOW THE URL THE SERVER MINTED. The claim creates the admin-setup scaffold and hands
        // back `/?s=<id>` — the setup conversation as a server record rather than a key in this
        // browser's storage, which is what made it vanish when the storage was cleared or a second
        // browser was used. A full navigation, not a flip: the claim changes what every probe on
        // this page would answer, so nothing may be left holding the old answer.
        const body = (await r.json().catch(() => ({}))) as { url?: string; scaffold_error?: string };
        if (body.scaffold_error) {
          // The role IS claimed and that is not undone. Only the conversation is missing, so say
          // exactly that instead of a generic failure the person would answer by re-claiming.
          setError(body.scaffold_error);
          return;
        }
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
        instance&rsquo;s administrator. You will write its company layer &mdash; who this company is, what it
        stands for, what it is working toward, and who can see what &mdash; and every agent working here
        carries what you write.
        Until that exists, this Vexa serves nobody.
      </div>
      <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 }}>
        There is no second administrator to undo this, so claim it only if the instance is yours to run.
      </div>
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.45 }}>{error}</div>}
      <button
        onClick={() => void claim()}
        disabled={busy}
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

/** ROW 3 — the instance has an administrator, the gate is up, and this is somebody else.
 *
 *  One sentence, the same sentence every other door uses, and a way out. What it deliberately does
 *  NOT do is imply the person did something wrong or that their account is broken: their session is
 *  fine, the instance simply is not open yet. */
function SetupRefusedCard({ email, onSignOut }: { email: string | null; onSignOut: () => void }) {
  return (
    <GateShell testId="setup-refused" title="Setting up this Vexa">
      <div style={{ fontSize: 12.5, color: "var(--t1)", lineHeight: 1.55 }}>{SETUP_GATE_NOTICE}</div>
      <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.6 }}>
        {email ? <>You&rsquo;re signed in as <strong style={{ color: "var(--t2)", fontWeight: 600 }}>{email}</strong>. </> : null}
        Until the administrator has written this instance&rsquo;s company layer, only they can use it.
        Your account is fine &mdash; reload this page once setup is finished and you&rsquo;ll be let in.
      </div>
      <button
        onClick={() => window.location.reload()}
        style={{
          background: "var(--panel2)", color: "var(--t1)", border: "1px solid var(--line2)", borderRadius: 7,
          padding: "9px 12px", fontSize: 13, fontWeight: 600, cursor: "pointer",
        }}
      >
        Check again
      </button>
      <button onClick={onSignOut} style={gateQuietBtn}>Sign out</button>
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
