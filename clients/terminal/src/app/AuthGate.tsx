"use client";
/** Login gate. Polls /api/auth/me on mount; if unauthenticated, renders the sign-in card.
 *  Primary path is OAuth — Google / Microsoft buttons (next-auth/react `signIn`, which works without a
 *  SessionProvider). Enabled providers are discovered from NextAuth's /api/auth/providers so a deploy
 *  with no OAuth creds simply hides the buttons. The direct email form is kept as a DEBUG path (server
 *  restricts it to addresses containing "test"), tucked behind a toggle. Styled to match the terminal
 *  (CSS vars from globals.css); does not redesign the workbench.
 *
 *  FIRST RUN: /api/auth/instance says whether an admin exists. On a fresh instance the card becomes
 *  the one-time "Set up your instance" claim screen — first sign-in becomes the admin — through
 *  whatever auth the deploy actually has: OAuth buttons when configured, otherwise the test-mode
 *  direct entry with an honest banner naming the OAuth upgrade path. */
import { useEffect, useState, type FormEvent } from "react";
import { signIn } from "next-auth/react";

type Status = "checking" | "out" | "in";
type Providers = { google: boolean; microsoft: boolean };

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [providers, setProviders] = useState<Providers>({ google: false, microsoft: false });
  const [adminExists, setAdminExists] = useState(true); // fail-safe: plain sign-in until told otherwise
  const [showDebug, setShowDebug] = useState(false);
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (active ? setStatus(r.ok ? "in" : "out") : undefined))
      .catch(() => active && setStatus("out"));
    // NextAuth lists configured providers here; absent/failed → just no OAuth buttons.
    fetch("/api/auth/providers", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((p: Record<string, unknown>) =>
        active && setProviders({ google: !!p.google, microsoft: !!p.microsoft }))
      .catch(() => undefined);
    // First-run probe — {admin_exists:false} flips the card into the admin-claim variant.
    fetch("/api/auth/instance", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { admin_exists: true }))
      .then((d: { admin_exists?: boolean }) => active && setAdminExists(d.admin_exists !== false))
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = email.trim();
    if (!value || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: value }),
      });
      if (r.ok) { window.location.reload(); return; }
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      setError(body.error || `Login failed (${r.status})`);
    } catch (err) {
      setError((err as Error).message || "Login failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "in") return <>{children}</>;
  if (status === "checking") return <div style={{ height: "100vh", background: "var(--bg)" }} />;

  const hasOAuth = providers.google || providers.microsoft;
  const claiming = !adminExists; // fresh instance → this sign-in claims the admin role

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
            {!hasOAuth && (
              <div
                style={{
                  fontSize: 11.5, lineHeight: 1.5, color: "var(--t2)", background: "var(--panel2)",
                  border: "1px solid var(--line2)", borderRadius: 8, padding: "9px 11px",
                }}
              >
                ⚠ Test mode — no OAuth configured. Sign-in is limited to emails containing &ldquo;test&rdquo;.
                For real authentication, acquire Google or Microsoft OAuth credentials and add them to this
                instance&rsquo;s environment (GOOGLE_CLIENT_ID/SECRET or MICROSOFT_CLIENT_ID/SECRET).
              </div>
            )}
          </>
        ) : (
          <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>Sign in to continue.</div>
        )}

        {providers.google && (
          <button onClick={() => signIn("google", { callbackUrl: window.location.pathname + window.location.search })} style={oauthBtn}>
            <GoogleMark /> Continue with Google
          </button>
        )}
        {providers.microsoft && (
          <button onClick={() => signIn("microsoft", { callbackUrl: window.location.pathname + window.location.search })} style={oauthBtn}>
            <MicrosoftMark /> Continue with Microsoft
          </button>
        )}

        {hasOAuth && (
          <button
            onClick={() => setShowDebug((v) => !v)}
            style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 11, cursor: "pointer", padding: 0, alignSelf: "flex-start" }}
          >
            {showDebug ? "Hide debug sign-in" : "Debug sign-in"}
          </button>
        )}

        {(!hasOAuth || showDebug) && (
          <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.4 }}>
              {claiming && !hasOAuth
                ? "Email — must contain “test” (test mode)."
                : "Debug login — email must contain “test”."}
            </div>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you-test@company.com"
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
              {submitting ? "Signing in…" : claiming ? "Sign in as admin" : "Sign in"}
            </button>
          </form>
        )}
        {claiming && (
          <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.4 }}>
            This claim screen disappears once an admin exists.
          </div>
        )}
      </div>
    </div>
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
