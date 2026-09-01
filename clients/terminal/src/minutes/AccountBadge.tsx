"use client";
/** The rail's foot: who is signed in, and the two controls that belong to a PERSON rather than to a
 *  room — the theme, and the way out.
 *
 *  Nothing here is new machinery. The identity seam is the full workbench's (`/api/auth/me`, backed
 *  by the vexa-user-info cookie) and so is the theme (`app/theme` writes `data-theme` on <html>;
 *  globals.css repaints every surface from the swapped variables, and layout.tsx applies the saved
 *  choice before first paint so day mode never flashes dark). Minutes mode had both underneath it
 *  the whole time and simply no door to either — this is the door, not a second implementation.
 *
 *  It lives INSIDE the rail, so collapsing the column takes the badge with it and the 22px edge
 *  handle stays a handle. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Icon } from "../ui-kit";
import { useTheme } from "../app/theme";
import { surface, type as ty } from "./tokens";

const itemS: CSSProperties = {
  ...ty.body,
  display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left",
  padding: "7px 9px", borderRadius: 7, border: "none", background: "transparent",
  color: "var(--t1)", cursor: "pointer",
};
const hi = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = surface.raisedHi; };
const lo = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = "transparent"; };

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

  // Wiping client state on the way out keeps the next person from inheriting this one's chats, tabs
  // and pane widths — the same discipline the workbench's profile row applies.
  const signOut = () => {
    void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage unavailable */ }
      window.location.reload();
    });
  };

  return (
    <div ref={box} style={{ position: "relative", flex: "none", borderTop: "1px solid var(--line)" }}>
      {open && (
        <div role="menu" data-acct="menu"
          style={{ position: "absolute", bottom: "calc(100% + 6px)", left: 8, right: 8, zIndex: 30, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 10, padding: 6, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>
          <button role="menuitem" data-acct="theme" onClick={() => { toggleTheme(); setOpen(false); }}
            style={itemS} onMouseEnter={hi} onMouseLeave={lo}>
            <Icon name={day ? "moon" : "sun"} size={14} />{day ? "Dark mode" : "Day mode"}
          </button>
          <button role="menuitem" data-acct="signout" onClick={signOut}
            style={itemS} onMouseEnter={hi} onMouseLeave={lo}>
            <Icon name="logout" size={14} />Sign out
          </button>
        </div>
      )}
      <button data-acct="badge" aria-haspopup="menu" aria-expanded={open} title={email || name}
        onClick={() => setOpen((v) => !v)}
        style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 10px", background: open ? surface.raised : "transparent", border: "none", cursor: "pointer", textAlign: "left" }}
        onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.background = "transparent"; }}>
        <span aria-hidden style={{ ...ty.control, fontSize: 10.5, width: 24, height: 24, borderRadius: "50%", flex: "none", background: surface.raisedHi, color: "var(--t1)", display: "flex", alignItems: "center", justifyContent: "center" }}>{initials}</span>
        <span style={{ minWidth: 0, flex: 1, lineHeight: 1.3 }}>
          <span style={{ ...ty.bodyStrong, fontSize: 12.5, color: "var(--t1)", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
          {email && <span style={{ ...ty.meta, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{email}</span>}
        </span>
        <span aria-hidden style={{ flex: "none", color: "var(--t3)", fontSize: 13, lineHeight: 1, fontFamily: "var(--sans)" }}>⋯</span>
      </button>
    </div>
  );
}
