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
    <div className="pt-1_5 pr-2 pb-1_5 pl-2 r-md t-xs c-2" style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <Icon name="key" size={13} />
      <div className="lh-tight" style={{ minWidth: 0, flex: 1 }}>
        <div className="c-1" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {token.name || `token #${token.id}`}
        </div>
        <div className="t-xs c-3">
          {token.scopes.map(scopeLabel).join(" · ")}{created ? ` · created ${created}` : ""}{expires ? ` · expires ${expires}` : ""}
        </div>
      </div>
      {confirming ? (
        <>
          <button onClick={() => onRevoke(token.id)} className="bg-none bd-none c-danger t-xs p-0_5" style={{ cursor: "pointer" }}>revoke</button>
          <button onClick={() => setConfirming(false)} className="bg-none bd-none c-3 t-xs p-0_5" style={{ cursor: "pointer" }}>keep</button>
        </>
      ) : (
        <button title="Revoke token" onClick={() => setConfirming(true)} className="bg-none bd-none c-3 p-0_5" style={{ cursor: "pointer", display: "flex" }}>
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
    <div className="mt-1 mr-1 mb-2 ml-1 p-2 r-md bd">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" className="mb-2" style={{ ...field }} />
      <div className="mb-2" style={{ display: "flex", gap: 10 }}>
        {TOKEN_SCOPES.map((s) => (
          <label key={s} title={`scope: ${s}`} className="t-xs c-2" style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />{scopeLabel(s)}
          </label>
        ))}
      </div>
      <select value={expiryIdx} onChange={(e) => setExpiryIdx(Number(e.target.value))} className="mb-2" style={{ ...field }}>
        {EXPIRIES.map((e, i) => <option key={e.label} value={i}>{e.label}</option>)}
      </select>
      {error && <div role="alert" className="t-xs c-danger mb-2">⚠ {error}</div>}
      <button onClick={() => void submit()} disabled={busy || scopes.length === 0}
        className="t-xs pt-1 pr-2 pb-1 pl-2 r-md bd bg-3 c-1" style={{ display: "flex", alignItems: "center", gap: 5, cursor: busy || scopes.length === 0 ? "default" : "pointer", opacity: busy || scopes.length === 0 ? 0.6 : 1 }}>
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
    <div className="p-2">
      {error && <div role="alert" className="t-xs c-danger pt-1_5 pr-2 pb-1_5 pl-2">⚠ Couldn’t load tokens — {error}</div>}
      {minted && <MintedTokenCard minted={minted} onDismiss={() => setMinted(null)} />}
      <CreateTokenForm onCreated={onCreated} />
      {tokens.map((t) => <TokenRow key={t.id} token={t} onRevoke={onRevoke} />)}
      {tokens.length === 0 && !error && <div className="pt-2 pr-1 pb-2 pl-1 c-3 t-xs">No API tokens yet.</div>}
    </div>
  );
}
