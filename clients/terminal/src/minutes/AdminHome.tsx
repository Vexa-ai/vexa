"use client";
/** THE ADMIN ACCOUNT'S HOME — four entry points, in the void an empty chat leaves.
 *
 *  Founder ruling 2026-10-11: the admin account is for *"global flows setup via mcp (… email invite
 *  and incoming outgoing emails control and setup) · templates control · friction reading from sink
 *  · global workspace setup"*. It has no desk, no workspaces and no meetings, so the everyday
 *  proposal chips (set up a workspace, send a bot) are offers it cannot take. This replaces them,
 *  for that account only.
 *
 *  EACH AREA IS A NAMED ASK, NOTHING COMPOSED HERE. A click follows `/?ask=<preset>` — the hand
 *  link the shell already mints server-side (`/api/scaffolds/hand`), so the words live in the
 *  admin-editable `_global/asks/<preset>.md` and the mount set is the server's (`_global` alone for
 *  this account). Same rule every other act on this screen keeps: the client sends names, never
 *  prompt text. */
import type { CSSProperties } from "react";
import { surface, type as ty } from "./tokens";

export interface AdminArea { key: string; label: string; ask: string; says: string }

/** The four areas, in the order the ruling names them. */
export const ADMIN_AREAS: readonly AdminArea[] = [
  { key: "flows-mail", label: "Flows & mail", ask: "mail-admin",
    says: "What the mailbox accepts and sends, and the flows that run" },
  { key: "templates", label: "Templates", ask: "templates",
    says: "The shapes new notes are created from" },
  { key: "friction", label: "Friction", ask: "friction-review",
    says: "Everything anybody here reported as not working" },
  { key: "workspace", label: "Workspace", ask: "setup-global",
    says: "The company layer every agent carries" },
];

export const adminAreaHref = (a: AdminArea): string => `/?ask=${encodeURIComponent(a.ask)}`;

const card: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 3, textDecoration: "none", textAlign: "left",
  background: surface.raised, border: "1px solid var(--line)", borderRadius: 10,
  padding: "10px 13px", minWidth: 0, transition: "background .12s, border-color .12s",
};

export function AdminHome() {
  return (
    <nav data-admin-home aria-label="Admin account"
      style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
               gap: 8, marginTop: 20 }}>
      {ADMIN_AREAS.map((a) => (
        <a key={a.key} data-admin-area={a.key} href={adminAreaHref(a)} style={card}
          onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.borderColor = "var(--accent)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.borderColor = "var(--line)"; }}>
          <span style={{ ...ty.body, fontWeight: 600, color: "var(--t1)" }}>{a.label}</span>
          <span style={{ ...ty.meta }}>{a.says}</span>
        </a>
      ))}
    </nav>
  );
}
