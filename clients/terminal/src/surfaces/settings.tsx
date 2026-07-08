"use client";
/** Settings — the footer-gear CENTER tab (design-spec meeting-lifecycle-v2, W5): account-level
 *  configuration in one place — Calendar integration, API tokens, GitHub token, Account. The old
 *  "API Tokens" activity-bar item retired into here (its panels are imported, not duplicated);
 *  the Meetings sidebar keeps its own calendar connect UI at the point of need — this is the
 *  durable home. Sections are a left nav (no sub-routing; one tab, local state). */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { registerTab } from "../contributions";
import { Icon } from "../ui-kit";
import { GitHubTokenCard, TokensPanel } from "./tokens";
import { getCalendarConfig, setCalendarConfig, getCalendarSyncStatus, syncCalendarNow, type CalendarConfig, type CalendarSyncStamp } from "./plannedApi";

type SectionId = "calendar" | "tokens" | "github" | "account";
const SECTIONS: Array<{ id: SectionId; label: string; icon: string }> = [
  { id: "calendar", label: "Calendar", icon: "cal" },
  { id: "tokens", label: "API tokens", icon: "key" },
  { id: "github", label: "GitHub", icon: "github" },
  { id: "account", label: "Account", icon: "user" },
];

const field: CSSProperties = { width: "100%", boxSizing: "border-box", fontSize: 12, padding: "6px 9px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" };
const btn: CSSProperties = { fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: "pointer" };

/** Calendar integration — the ICS feed + the global auto-join default. Same API the Meetings
 *  sidebar's connect button uses (identity admin-api via the gateway); errors stay loud. */
function CalendarSection() {
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [stamp, setStamp] = useState<CalendarSyncStamp | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => {
    getCalendarConfig().then((c) => { setCfg(c); setErr(null); }).catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
    getCalendarSyncStatus().then(setStamp).catch(() => undefined);
  };
  useEffect(refresh, []);

  const save = async (body: { ics_url?: string | null; auto_join?: boolean }) => {
    setBusy(true); setErr(null);
    try { setCfg(await setCalendarConfig(body)); setUrl(""); refresh(); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const syncNow = async () => {
    setSyncing(true); setErr(null);
    try { setStamp(await syncCalendarNow()); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setSyncing(false); }
  };

  const connected = !!cfg?.ics_url_set;
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.5, marginBottom: 12, maxWidth: 460 }}>
        Connect your calendar's secret ICS feed and scheduled meetings appear in Meetings by themselves;
        with auto-join on, the bot joins them when they start.
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", marginBottom: 10 }}>⚠ {err}</div>}
      {connected ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 460 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="cal" size={13} style={{ color: "var(--green)" }} />
            <span style={{ flex: 1, fontSize: 12.5, color: "var(--t2)", fontFamily: "var(--mono)" }}>{cfg?.ics_url_masked ?? "connected"}</span>
            <button disabled={busy || syncing} onClick={() => void syncNow()} style={btn}>{syncing ? "Syncing…" : "Sync now"}</button>
            <button disabled={busy || syncing} onClick={() => void save({ ics_url: null })} style={{ ...btn, color: "var(--danger)" }}>Disconnect</button>
          </div>
          {stamp?.last_error
            ? <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.5 }}>⚠ Last sync failed: {stamp.last_error}</div>
            : stamp?.last_sync && <div style={{ fontSize: 11, color: "var(--t3)" }}>Last synced {new Date(stamp.last_sync).toLocaleString()}</div>}
          <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5, color: "var(--t2)", cursor: "pointer" }}>
            <input type="checkbox" checked={cfg?.auto_join !== false} disabled={busy}
              onChange={(e) => void save({ auto_join: e.target.checked })} />
            Auto-join — send the bot to calendar meetings that have a link
          </label>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, maxWidth: 460 }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://calendar.google.com/…/basic.ics (secret address)"
            onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) void save({ ics_url: url.trim() }); }} style={field} />
          <button disabled={busy || !url.trim()} onClick={() => void save({ ics_url: url.trim() })}
            style={{ ...btn, background: "var(--accent)", color: "var(--on-accent)", border: "none", opacity: busy || !url.trim() ? 0.5 : 1, flex: "none" }}>
            {busy ? "Connecting…" : "Connect"}
          </button>
        </div>
      )}
    </div>
  );
}

function AccountSection() {
  const [user, setUser] = useState<{ email?: string | null; name?: string | null } | null>(null);
  useEffect(() => {
    let on = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => on && setUser((d?.user as { email?: string; name?: string } | undefined) ?? null))
      .catch(() => undefined);
    return () => { on = false; };
  }, []);
  return (
    <div style={{ fontSize: 12.5, color: "var(--t2)", lineHeight: 1.9 }}>
      <div><span style={{ color: "var(--t3)" }}>Signed in as</span> <span style={{ color: "var(--t1)" }}>{user?.name || user?.email || "…"}</span></div>
      {user?.email && <div><span style={{ color: "var(--t3)" }}>Email</span> <span style={{ fontFamily: "var(--mono)" }}>{user.email}</span></div>}
      <div style={{ color: "var(--t3)", marginTop: 6 }}>Theme and sign-out live next to your name in the footer.</div>
    </div>
  );
}

function SettingsView() {
  const [section, setSection] = useState<SectionId>("calendar");
  const bodies: Record<SectionId, ReactNode> = {
    calendar: <CalendarSection />,
    tokens: <TokensPanel />,
    github: <GitHubTokenCard />,
    account: <AccountSection />,
  };
  return (
    <div style={{ height: "100%", display: "flex", minHeight: 0 }}>
      <div style={{ width: 160, flex: "none", borderRight: "1px solid var(--line)", padding: "14px 8px", background: "var(--sidebar)" }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--t1)", padding: "0 8px 10px" }}>Settings</div>
        {SECTIONS.map((s) => (
          <button key={s.id} onClick={() => setSection(s.id)}
            style={{ display: "flex", alignItems: "center", gap: 7, width: "100%", textAlign: "left", fontSize: 12.5,
              padding: "6px 9px", borderRadius: 7, border: "none", cursor: "pointer",
              color: section === s.id ? "var(--t1)" : "var(--t2)", background: section === s.id ? "var(--panel2)" : "transparent" }}>
            <Icon name={s.icon} size={13} />{s.label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 22px", minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--t1)", marginBottom: 12 }}>
          {SECTIONS.find((s) => s.id === section)?.label}
        </div>
        {bodies[section]}
      </div>
    </div>
  );
}

registerTab("settings", SettingsView);
