"use client";
/** Account actions; all service setup lives in Connections. */
import { useEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { Icon } from "../ui-kit";
import { useTheme } from "../platform/theme";
import { UsageMeter } from "../surfaces/usage/Usage";
import { CONNECTIONS_OPEN } from "./connectionEvents";
import { readActiveSet } from "../data/workspaceApi";
import { TestUsersDialog } from "./TestUsers";
import { SettingsDialog } from "./SettingsDialog";
import { Identity } from "../ui-kit/Identity";
import { PHOTO_CHANGED_EVENT, changeOwnPhoto, photoUrl, readOwnPhoto, type OwnPhoto } from "../surfaces/personPhotoApi";
import { useFeature } from "../platform/features";
import { openMyProfile } from "./shell2/personOpen";
import { Modal } from "../ui-kit/Modal";
import { DirectorySearch } from "../surfaces/profile/PublicProfile";
import { applyCachedAppearance, refreshAppearance } from "../platform/appearance";

export function switchAccount(): void {
  void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
    window.location.reload();
  });
}

/** Move this tab between the person and the company (ADR-0049 § The Company profile): the server
 *  swaps the session (`/api/auth/company`), and the page reloads so every surface reads the new one. */
export function switchProfile(to: "company" | "me"): Promise<boolean> {
  return fetch("/api/auth/company", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to }),
  }).then((r) => {
    if (r.ok) window.location.reload();
    return r.ok;
  }).catch(() => false);
}

interface Me {
  user?: { email?: string | null; name?: string | null };
  /** This tab works as the company; `person` is who it switches back to. */
  company?: { person?: { email?: string | null; name?: string | null } } | null;
  /** app.dev tooling (#1783): the account menu offers "Test users…" */
  dev_test_users?: boolean;
  /** An instance administrator on a deployment with an admin account. */
  can_work_as_company?: boolean;
}

export function AccountBadge() {
  const [me, setMe] = useState<Me | null>(null);
  const [testUsers, setTestUsers] = useState(false);
  const [settings, setSettings] = useState(false);
  const [directory, setDirectory] = useState(false);
  const profilesOn = useFeature("public_profiles");
  const openOn = useFeature("open_workspaces");
  const shell2 = useFeature("shell2");
  // THE PERSON'S APPEARANCE (Settings → Appearance): the last one this browser saw at once, then the
  // server's, and again whenever the window comes back (they may have changed it on another device)
  useEffect(() => {
    applyCachedAppearance();
    void refreshAppearance();
    const on = () => { if (document.visibilityState === "visible") void refreshAppearance(); };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const user = me?.user ?? null;
  // In Company mode the card names the company: the `_global` layer's display name when it has one.
  const [companyName, setCompanyName] = useState<string | null>(null);
  useEffect(() => {
    if (!me?.company) return;
    let active = true;
    readActiveSet()
      .then((set) => {
        const g = (set?.active ?? []).find((m: { slug?: string }) => m.slug === "_global") as { name?: string | null } | undefined;
        if (active) setCompanyName(g?.name?.trim() || null);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [me?.company]);
  const [open, setOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => active && setMe((d as Me | null) ?? null))
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  // A menu that outlives its own dismissal is worse than no menu: click-away and Escape both close.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  // THE PERSON'S OWN FACE (UI 15g): their photo route, re-asked with its version when it changes.
  const [own, setOwn] = useState<OwnPhoto | null>(null);
  useEffect(() => {
    let live = true;
    void readOwnPhoto().then((p) => { if (live && p && typeof p.subject === "string") setOwn(p); });
    const onChanged = (e: Event) => { const p = (e as CustomEvent<OwnPhoto>).detail; if (p?.subject) setOwn(p); };
    window.addEventListener(PHOTO_CHANGED_EVENT, onChanged);
    return () => { live = false; window.removeEventListener(PHOTO_CHANGED_EVENT, onChanged); };
  }, []);

  const company = me?.company ?? null;
  const person = company?.person ?? null;
  const personName = (person?.name || (person?.email ? person.email.split("@")[0] : "") || "me").trim();
  const email = company ? "Company" : (user?.email ?? "");
  const name = company ? (companyName || "Company") : (user?.name || (user?.email ? user.email.split("@")[0] : "") || "Account").trim();
  const flip = (to: "company" | "me") => {
    setSwitchError(null);
    void switchProfile(to).then((ok) => { if (!ok) setSwitchError(to === "company" ? "Could not switch to Company" : "Could not switch back"); });
  };
  const initials = (name.match(/\b[a-z0-9]/gi) || []).slice(0, 2).join("").toUpperCase() || "?";
  const day = theme === "light";

  const signOut = switchAccount;

  // The account menu on the ui-kit menu classes (guidelines §4.4): 28px items with icons, the
  // overlay surface and shadow tokens, focus rings; the trigger is a ListRow-height button with
  // the avatar, the name and the address truncating with an ellipsis.
  return (
    <div ref={box} className="vx-acct">
      {open && (
        <div role="menu" data-acct="menu" className="vx-menu vx-acct-menu">
          {/* shell2: your own profile opens like anyone's — the person panel, with Edit */}
          {shell2 && !company && (
            <button role="menuitem" data-acct="profile" className="vx-menu-item" onClick={() => { setOpen(false); openMyProfile(name); }}>
              <span className="vx-menu-icon" aria-hidden><Icon name="user" size={14} /></span><span className="vx-menu-label">Your profile</span>
            </button>
          )}
          {me?.can_work_as_company === true && !company && (
            <button role="menuitem" data-acct="company" className="vx-menu-item" onClick={() => flip("company")}>
              <span className="vx-menu-icon" aria-hidden><Icon name="building" size={14} /></span><span className="vx-menu-label">Switch to Company</span>
            </button>
          )}
          {company && (
            <button role="menuitem" data-acct="person" className="vx-menu-item" onClick={() => flip("me")}>
              <span className="vx-menu-icon" aria-hidden><Icon name="user" size={14} /></span><span className="vx-menu-label">Switch back to {personName}</span>
            </button>
          )}
          {switchError && <div role="alert" className="vx-menu-item">{switchError}</div>}
          {(company || me?.can_work_as_company === true) && <div role="separator" className="vx-menu-sep" />}
          <button role="menuitem" className="vx-menu-item" onClick={() => { window.dispatchEvent(new Event(CONNECTIONS_OPEN)); setOpen(false); }}>
            <span className="vx-menu-icon" aria-hidden><Icon name="link" size={14} /></span><span className="vx-menu-label">Connections</span>
          </button>
          {me?.dev_test_users === true && (
            <button role="menuitem" data-acct="test-users" className="vx-menu-item" onClick={() => { setTestUsers(true); setOpen(false); }}>
              <span className="vx-menu-icon" aria-hidden><Icon name="user" size={14} /></span><span className="vx-menu-label">Test users…</span>
            </button>
          )}
          {own && !company && (
            <button role="menuitem" data-acct="photo" className="vx-menu-item" onClick={() => { setOpen(false); void changeOwnPhoto(); }}>
              <span className="vx-menu-icon" aria-hidden><Icon name="user" size={14} /></span><span className="vx-menu-label">Change photo…</span>
            </button>
          )}
          {(profilesOn || openOn) && (
            <button role="menuitem" data-acct="directory" className="vx-menu-item" onClick={() => { setDirectory(true); setOpen(false); }}>
              <span className="vx-menu-icon" aria-hidden><Icon name="user" size={14} /></span><span className="vx-menu-label">Directory</span>
            </button>
          )}
          <button role="menuitem" data-acct="settings" className="vx-menu-item" onClick={() => { setSettings(true); setOpen(false); }}>
            <span className="vx-menu-icon" aria-hidden><Icon name="gear" size={14} /></span><span className="vx-menu-label">Settings</span>
          </button>
          <button role="menuitem" data-acct="theme" className="vx-menu-item" onClick={() => { toggleTheme(); setOpen(false); }}>
            <span className="vx-menu-icon" aria-hidden><Icon name={day ? "moon" : "sun"} size={14} /></span><span className="vx-menu-label">{day ? "Dark mode" : "Day mode"}</span>
          </button>
          <div role="separator" className="vx-menu-sep" />
          <button role="menuitem" data-acct="signout" className="vx-menu-item" onClick={signOut}>
            <span className="vx-menu-icon" aria-hidden><Icon name="logout" size={14} /></span><span className="vx-menu-label">Sign out</span>
          </button>
        </div>
      )}
      {/* VEXA CREDITS (`usage`): "42% · 5h · 18% · week" beside the account; hidden while off */}
      <UsageMeter />
      <button data-acct="badge" data-profile={company ? "company" : "person"} className="vx-acct-badge" aria-haspopup="menu" aria-expanded={open} title={email || name}
        onClick={() => setOpen((v) => !v)}>
        {own && !company
          ? <Identity kind="person" id={own.subject} name={name} user size={24} className="vx-acct-face"
              src={own.photo ? photoUrl(own.subject, own.photo.version) : undefined} />
          : <span aria-hidden className="vx-acct-avatar">{initials}</span>}
        <span className="vx-acct-text">
          <span className="vx-acct-name">{name}</span>
          {email && <span className="vx-acct-email">{email}</span>}
        </span>
        <Ellipsis size={16} strokeWidth={1.75} aria-hidden className="vx-acct-more" />
      </button>
      {testUsers && <TestUsersDialog open={testUsers} onClose={() => setTestUsers(false)} />}
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
      {directory && <Modal title="Directory" onClose={() => setDirectory(false)} width={560}><DirectorySearch /></Modal>}
    </div>
  );
}
