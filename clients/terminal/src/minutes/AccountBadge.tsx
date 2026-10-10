"use client";
/** Account actions; all service setup lives in Connections. */
import { useEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { Icon } from "../ui-kit";
import { useTheme } from "../app/theme";
import { CONNECTIONS_OPEN } from "./connectionEvents";

export function switchAccount(): void {
  void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
    window.location.reload();
  });
}

export function AccountBadge() {
  const [user, setUser] = useState<{ email?: string | null; name?: string | null } | null>(null);
  const [open, setOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => active && setUser((d?.user as { email?: string; name?: string } | undefined) ?? null))
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

  const email = user?.email ?? "";
  const name = (user?.name || (email ? email.split("@")[0] : "") || "Account").trim();
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
          <button role="menuitem" className="vx-menu-item" onClick={() => { window.dispatchEvent(new Event(CONNECTIONS_OPEN)); setOpen(false); }}>
            <span className="vx-menu-icon" aria-hidden><Icon name="link" size={14} /></span><span className="vx-menu-label">Connections</span>
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
      <button data-acct="badge" className="vx-acct-badge" aria-haspopup="menu" aria-expanded={open} title={email || name}
        onClick={() => setOpen((v) => !v)}>
        <span aria-hidden className="vx-acct-avatar">{initials}</span>
        <span className="vx-acct-text">
          <span className="vx-acct-name">{name}</span>
          {email && <span className="vx-acct-email">{email}</span>}
        </span>
        <Ellipsis size={16} strokeWidth={1.75} aria-hidden className="vx-acct-more" />
      </button>
    </div>
  );
}
