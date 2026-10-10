"use client";
/** API tokens + GitHub token panels — the user's credential self-serve (list, mint, revoke; save-once
 *  PAT). These render inside the SETTINGS tab (surfaces/settings.tsx — the footer-gear surface,
 *  design-spec meeting-lifecycle-v2 W5); the old "API Tokens" activity-bar item is retired so
 *  integrator config leaves the daily-driver nav. All data flows through /api/tokens, which resolves
 *  the user server-side from the auth cookies — no user_id ever leaves this component (P20). The
 *  minted token value is shown ONCE (copy it now); it is never listed again.
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Icon, SecretReveal } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { listTokens, createToken, revokeToken, TOKEN_SCOPES, type TokenInfo, type TokenScope, type MintedToken } from "./tokensApi";
import { presentError } from "./apiClient";

const EXPIRIES: Array<{ label: string; seconds?: number }> = [
  { label: "never expires" },
  { label: "1 hour", seconds: 3600 },
  { label: "24 hours", seconds: 86400 },
  { label: "30 days", seconds: 30 * 86400 },
  { label: "90 days", seconds: 90 * 86400 },
];

const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : null);

// Scopes speak CAPABILITIES to the user (the raw scope id rides in the tooltip + the API).
const SCOPE_LABELS: Record<string, string> = { bot: "Join meetings", tx: "Read transcripts", browser: "Browse web" };
const scopeLabel = (s: string) => SCOPE_LABELS[s] ?? s;

function TokenRow({ token, onRevoke }: { token: TokenInfo; onRevoke: (id: number) => void }) {
  const [confirming, setConfirming] = useState(false);
  const created = fmtDate(token.created_at);
  const expires = fmtDate(token.expires_at);
  return (
    <div style={{ padding: "7px 9px", borderRadius: 6, display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--t2)" }}>
      <Icon name="key" size={13} />
      <div style={{ minWidth: 0, flex: 1, lineHeight: 1.3 }}>
        <div style={{ color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {token.name || `token #${token.id}`}
        </div>
        <div style={{ fontSize: 11, color: "var(--t3)" }}>
          {token.scopes.map(scopeLabel).join(" · ")}{created ? ` · created ${created}` : ""}{expires ? ` · expires ${expires}` : ""}
        </div>
      </div>
      {confirming ? (
        <>
          <button onClick={() => onRevoke(token.id)} style={{ background: "none", border: "none", color: "var(--danger)", cursor: "pointer", fontSize: 11.5, padding: 2 }}>revoke</button>
          <button onClick={() => setConfirming(false)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 11.5, padding: 2 }}>keep</button>
        </>
      ) : (
        <button title="Revoke token" onClick={() => setConfirming(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 2 }}>
          <Icon name="x" size={13} />
        </button>
      )}
    </div>
  );
}

/** The one-time reveal: shown right after a mint, then gone forever (the list never carries the value). */
function MintedTokenCard({ minted, onDismiss }: { minted: MintedToken; onDismiss: () => void }) {
  // THE TOKEN IS SHOWN THROUGH SecretReveal (guidelines §7 S1): masked until the person presses
  // Show, copied only on an explicit press, and never written into an attribute, a URL or storage.
  return (
    <div className="vx-card" data-raised="" role="status">
      <p className="vx-dialog-text">Token created — copy it now; it will <b>not</b> be shown again.</p>
      <div className="vx-gap-top"><SecretReveal value={minted.token} label="new API token" onCopy={(v) => copyText(v)} /></div>
      <div className="vx-gap-top"><Button variant="ghost" onClick={onDismiss}>Done</Button></div>
    </div>
  );
}

function CreateTokenForm({ onCreated }: { onCreated: (t: MintedToken) => void }) {
  const [scopes, setScopes] = useState<TokenScope[]>(["bot", "tx", "browser"]);
  const [name, setName] = useState("");
  const [expiryIdx, setExpiryIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (s: TokenScope) =>
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));

  const submit = async () => {
    if (scopes.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const minted = await createToken({ scopes, name: name.trim() || undefined, expiresIn: EXPIRIES[expiryIdx].seconds });
      setName("");
      onCreated(minted);
    } catch (e: unknown) {
      setError(presentError(e).headline);  // fail-loud (P18)
    } finally {
      setBusy(false);
    }
  };

  const field = { width: "100%", fontSize: 12, padding: "5px 8px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" } as const;
  return (
    <div style={{ margin: "4px 4px 10px", padding: 10, borderRadius: 8, border: "1px solid var(--line)" }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" style={{ ...field, marginBottom: 8 }} />
      <div style={{ display: "flex", gap: 10, marginBottom: 8 }}>
        {TOKEN_SCOPES.map((s) => (
          <label key={s} title={`scope: ${s}`} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--t2)", cursor: "pointer" }}>
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />{scopeLabel(s)}
          </label>
        ))}
      </div>
      <select value={expiryIdx} onChange={(e) => setExpiryIdx(Number(e.target.value))} style={{ ...field, marginBottom: 8 }}>
        {EXPIRIES.map((e, i) => <option key={e.label} value={i}>{e.label}</option>)}
      </select>
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", marginBottom: 8 }}>⚠ {error}</div>}
      <button onClick={() => void submit()} disabled={busy || scopes.length === 0}
        style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: busy || scopes.length === 0 ? "default" : "pointer", opacity: busy || scopes.length === 0 ? 0.6 : 1 }}>
        <Icon name="plus" size={12} />{busy ? "creating…" : "Create token"}
      </button>
    </div>
  );
}

/** Settings uses the same connection editor as Minutes. */
export { GitConnection as GitHubTokenCard } from '../minutes/GitConnection';

export function TokensPanel() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18)

  const refresh = useCallback(() => {
    void listTokens().then((t) => { setTokens(t); setError(null); }).catch((e: unknown) => setError(presentError(e).headline));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const onCreated = (t: MintedToken) => { setMinted(t); refresh(); };
  const onRevoke = (id: number) => {
    void revokeToken(id).then(refresh).catch((e: unknown) => setError(presentError(e).headline));
  };

  return (
    <div style={{ padding: "8px" }}>
      {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "6px 9px" }}>⚠ Couldn’t load tokens — {error}</div>}
      {minted && <MintedTokenCard minted={minted} onDismiss={() => setMinted(null)} />}
      <CreateTokenForm onCreated={onCreated} />
      {tokens.map((t) => <TokenRow key={t.id} token={t} onRevoke={onRevoke} />)}
      {tokens.length === 0 && !error && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No API tokens yet.</div>}
    </div>
  );
}
