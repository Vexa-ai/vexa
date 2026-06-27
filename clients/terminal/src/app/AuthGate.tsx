"use client";
/** Minimal login gate. Polls /api/auth/me on mount; if unauthenticated, renders a small email-entry
 *  form that POSTs /api/auth/login then reloads into the workbench. Direct email login — no password,
 *  no SMTP. Styled to match the terminal (CSS vars from globals.css); does not redesign the workbench. */
import { useEffect, useState, type FormEvent } from "react";

type Status = "checking" | "out" | "in";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (active ? setStatus(r.ok ? "in" : "out") : undefined))
      .catch(() => active && setStatus("out"));
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

  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <form
        onSubmit={submit}
        style={{
          width: 320, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 12,
          padding: 24, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)",
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)" }}>Vexa Terminal</div>
        <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>Enter your email to sign in.</div>
        <input
          type="email"
          autoFocus
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
          style={{
            background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 7,
            padding: "9px 10px", color: "var(--t1)", fontSize: 13, outline: "none",
          }}
        />
        {error && <div style={{ fontSize: 11, color: "var(--live)", lineHeight: 1.4 }}>{error}</div>}
        <button
          type="submit"
          disabled={!email.trim() || submitting}
          style={{
            background: email.trim() ? "var(--accent)" : "var(--panel2)",
            color: email.trim() ? "var(--on-accent, #241008)" : "var(--t3)",
            border: "none", borderRadius: 7, padding: "9px 10px", fontSize: 13, fontWeight: 600,
            cursor: email.trim() && !submitting ? "pointer" : "default",
          }}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
