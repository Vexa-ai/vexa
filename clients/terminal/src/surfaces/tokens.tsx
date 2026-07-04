"use client";
/** API Tokens — the logged-in user's token self-serve LIST (left): list, mint, revoke.
 *
 *  Registers in EVERY terminal mode (it's the settings surface meetings-only deployments need to get
 *  programmatic API access). All data flows through /api/tokens, which resolves the user server-side
 *  from the auth cookies — no user_id ever leaves this component (P20). The minted token value is
 *  shown ONCE (copy it now); it is never listed again.
 */
import { useCallback, useEffect, useState } from "react";
import { registerList } from "../contributions";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { listTokens, createToken, revokeToken, TOKEN_SCOPES, type TokenInfo, type TokenScope, type MintedToken } from "./tokensApi";

const EXPIRIES: Array<{ label: string; seconds?: number }> = [
  { label: "never expires" },
  { label: "1 hour", seconds: 3600 },
  { label: "24 hours", seconds: 86400 },
  { label: "30 days", seconds: 30 * 86400 },
  { label: "90 days", seconds: 90 * 86400 },
];

const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : null);

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
          {token.scopes.join(" · ")}{created ? ` · created ${created}` : ""}{expires ? ` · expires ${expires}` : ""}
        </div>
      </div>
      {confirming ? (
        <>
          <button onClick={() => onRevoke(token.id)} style={{ background: "none", border: "none", color: "var(--live)", cursor: "pointer", fontSize: 11.5, padding: 2 }}>revoke</button>
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
  const [copied, setCopied] = useState(false);
  const copy = () => { copyText(minted.token); setCopied(true); };
  return (
    <div style={{ margin: "8px 4px", padding: 10, borderRadius: 8, border: "1px solid var(--line)", background: "var(--panel2)" }}>
      <div style={{ fontSize: 11.5, color: "var(--t2)", marginBottom: 6 }}>
        Token created — copy it now, it will <b>not</b> be shown again.
      </div>
      <code style={{ display: "block", fontSize: 11, color: "var(--t1)", wordBreak: "break-all", marginBottom: 8 }}>{minted.token}</code>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={copy} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, padding: "3px 8px", borderRadius: 6, border: "1px solid var(--line)", background: "transparent", color: "var(--t1)", cursor: "pointer" }}>
          <Icon name="copy" size={12} />{copied ? "copied" : "copy"}
        </button>
        <button onClick={onDismiss} style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: 6, border: "none", background: "transparent", color: "var(--t3)", cursor: "pointer" }}>done</button>
      </div>
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
      setError(e instanceof Error ? e.message : String(e));  // fail-loud (P18)
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
          <label key={s} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--t2)", cursor: "pointer" }}>
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />{s}
          </label>
        ))}
      </div>
      <select value={expiryIdx} onChange={(e) => setExpiryIdx(Number(e.target.value))} style={{ ...field, marginBottom: 8 }}>
        {EXPIRIES.map((e, i) => <option key={e.label} value={i}>{e.label}</option>)}
      </select>
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--live)", marginBottom: 8 }}>⚠ {error}</div>}
      <button onClick={() => void submit()} disabled={busy || scopes.length === 0}
        style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: busy || scopes.length === 0 ? "default" : "pointer", opacity: busy || scopes.length === 0 ? 0.6 : 1 }}>
        <Icon name="plus" size={12} />{busy ? "creating…" : "Create token"}
      </button>
    </div>
  );
}

function TokensList() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18)

  const refresh = useCallback(() => {
    void listTokens().then((t) => { setTokens(t); setError(null); }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const onCreated = (t: MintedToken) => { setMinted(t); refresh(); };
  const onRevoke = (id: number) => {
    void revokeToken(id).then(refresh).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>api tokens</div>
      {error && <div role="alert" style={{ fontSize: 12, color: "var(--live)", padding: "6px 9px" }}>⚠ Couldn’t load tokens — {error}</div>}
      {minted && <MintedTokenCard minted={minted} onDismiss={() => setMinted(null)} />}
      <CreateTokenForm onCreated={onCreated} />
      {tokens.map((t) => <TokenRow key={t.id} token={t} onRevoke={onRevoke} />)}
      {tokens.length === 0 && !error && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No API tokens yet.</div>}
    </div>
  );
}

registerList({ id: "tokens", label: "API Tokens", icon: "key", order: 60, component: TokensList });
