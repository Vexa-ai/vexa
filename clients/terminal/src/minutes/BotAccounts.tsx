"use client";
/** Bot accounts — saved signed-in browsers a meeting bot can join as. Listed in the Connections page;
 *  signing one in opens the live browser view (BrowserSessionView). A company account is managed
 *  only from the admin account; everyone else sees it read-only. */
import { useCallback, useEffect, useState } from "react";
import { connectionStyle as cs } from "./connectionStyles";
import { type as ty } from "./tokens";
import { useFeature } from "../platform/features";
import { BOT_ACCOUNTS_CHANGED, PLATFORM_NAMES, startBrowserSession, type BotPlatform } from "./browserSession";

type BotAccount = { id: string; owner: string; platform: BotPlatform; display_name: string; state: "ok" | "signed_out"; usable?: boolean; mine?: boolean };
const PLATFORMS: BotPlatform[] = ["google", "microsoft", "zoom"];
const isCompany = (a: BotAccount) => a.owner === "company";

/** Hidden while `browser_bots` is switched off (`platform/features`). */
export function BotAccounts() {
  return useFeature("browser_bots") ? <BotAccountsBody /> : null;
}

function BotAccountsBody() {
  const [accounts, setAccounts] = useState<BotAccount[]>([]);
  const [admin, setAdmin] = useState(false);
  const [platform, setPlatform] = useState<BotPlatform>("google");
  const [error, setError] = useState(""), [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false), [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/bot-accounts", { cache: "no-store" });
      if (!r.ok) { setUnavailable(true); return; }
      const data = await r.json();
      setUnavailable(false);
      setAccounts(Array.isArray(data?.accounts) ? data.accounts : []);
    } catch { setUnavailable(true); }
  }, []);

  useEffect(() => {
    void load();
    fetch("/api/auth/me", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((me) => setAdmin(me?.admin_account === true)).catch(() => {});
    const changed = () => void load();
    window.addEventListener(BOT_ACCOUNTS_CHANGED, changed);
    return () => window.removeEventListener(BOT_ACCOUNTS_CHANGED, changed);
  }, [load]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const canManage = (a: BotAccount) => (isCompany(a) ? admin : a.mine !== false);
  const signIn = (p: BotPlatform, a?: BotAccount) => run(async () => {
    const owner = a ? (isCompany(a) ? "company" : "me") : admin ? "company" : undefined;
    await startBrowserSession(p, owner, a?.id);
  });
  const remove = (a: BotAccount) => run(async () => {
    const r = await fetch(`/api/bot-accounts/${a.id}`, { method: "DELETE", cache: "no-store" });
    if (!r.ok) throw new Error("Could not delete this bot account. Please try again.");
    setDeleting(null);
    setAccounts((prev) => prev.filter((row) => row.id !== a.id));
  });

  const button = { ...cs.button, cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1 };
  // the select drops the input's bottom gap: it sits in a row of controls
  const { marginBottom: _inputGap, ...selectStyle } = cs.input;
  return <section aria-label="Bot accounts" style={cs.card}>
    <h3 className="mt-0 mr-0 mb-1_5 ml-0" style={{ ...ty.title }}>Bot accounts</h3>
    <p className="c-2 mt-0 mr-0 mb-4 ml-0" style={{ ...ty.body }}>
      Signed-in accounts your meeting bots can join as. You sign in yourself, in a browser shown here.
    </p>
    {error && <p role="alert" className="p-3 bd-danger r-md c-1">{error}</p>}
    {unavailable && <p style={{ ...ty.meta }}>Bot accounts are not available on this deployment.</p>}
    {accounts.map((a) => <div key={a.id} data-bot-account={a.id} className="bd-t pt-3 pr-0 pb-3 pl-0">
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ ...ty.bodyStrong }}>{a.display_name}</div>
          <div className="mt-1" style={{ ...ty.meta }}>{PLATFORM_NAMES[a.platform] ?? a.platform} · {isCompany(a) ? "Company" : "Me"}</div>
        </div>
        <span data-bot-state={a.state} className={`pt-0_5 pr-2 pb-0_5 pl-2 r-full bg-2 ${a.state === "ok" ? "c-success" : "c-danger"}`} style={{ ...ty.pill }}>
          {a.state === "ok" ? "OK" : "Signed out"}
        </span>
        {canManage(a) && a.state === "signed_out" && <button disabled={busy} style={{ ...button, ...cs.primary }} onClick={() => void signIn(a.platform, a)}>Sign in again</button>}
        {canManage(a) && <button aria-label={`Delete ${a.display_name}`} disabled={busy} className="bg-none c-danger" style={{ ...button }} onClick={() => setDeleting(a.id)}>Delete</button>}
      </div>
      {deleting === a.id && <div role="alert" className="mt-3 p-3 bg-2" style={{ ...ty.body }}>
        Delete this bot account? Its saved sign-in is destroyed; bots stop joining as it.
        <div className="mt-2" style={{ display: "flex", gap: 8 }}>
          <button disabled={busy} className="c-danger" style={{ ...button }} onClick={() => void remove(a)}>Delete bot account</button>
          <button disabled={busy} style={button} onClick={() => setDeleting(null)}>Cancel</button>
        </div>
      </div>}
    </div>)}
    {!unavailable && <div className="mt-3" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <select aria-label="Bot account platform" value={platform} onChange={(e) => setPlatform(e.target.value as BotPlatform)} style={{ ...selectStyle, width: "auto" }}>
        {PLATFORMS.map((p) => <option key={p} value={p}>{PLATFORM_NAMES[p]}</option>)}
      </select>
      <button disabled={busy} style={button} onClick={() => void signIn(platform)}>Sign in a bot account</button>
      {admin && <span style={{ ...ty.meta }}>Saved for the company.</span>}
    </div>}
  </section>;
}
