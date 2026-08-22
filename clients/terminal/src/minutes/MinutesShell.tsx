"use client";
/** MINUTES — the one-object shell (#1311).
 *
 *  Everything is a ROOM: a page you can read, a conversation you can have, a context (the set of
 *  workspaces mounted into the agent), and a flavor. Meetings are rooms that happened. The rail is
 *  two LENSES over one set — Meetings by time, Rooms by name — and every row opens the same
 *  screen: the room's page (right), its conversation (center), its context named in the header.
 *
 *  This replaces the dockview Workbench in minutes mode only; the full workbench is untouched.
 *  Conversations are REAL per-room threads: each room maps to an agent session id, so switching
 *  rooms switches the unit (and its worker) — chat follows the workspace, never the document.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import {
  listSharedMemberships, readActiveSet, setSharedActive, deactivateWorkspace,
  readWorkspaceFile, type Membership,
} from "../surfaces/workspaceApi";
import { MdxDoc } from "../ui-kit/MdxDoc";

type Sel = { kind: "personal" | "shared" | "org" | "meeting"; id: string; label: string };

const S = {
  rail: { width: 252, flex: "none", borderRight: "1px solid var(--line2)", background: "var(--sidebar)", padding: "14px 10px", display: "flex", flexDirection: "column", gap: 18, overflowY: "auto" } as React.CSSProperties,
  h2: { fontSize: 10.5, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)", padding: "0 8px 4px", fontWeight: 600 } as React.CSSProperties,
  row: (on: boolean): React.CSSProperties => ({ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 7, color: on ? "var(--t1)" : "var(--t2)", cursor: "pointer", fontSize: 13, background: on ? "var(--panel2)" : "transparent", border: "none", width: "100%", textAlign: "left", fontFamily: "inherit" }),
  dot: (on: boolean): React.CSSProperties => ({ width: 7, height: 7, borderRadius: "50%", background: on ? "var(--accent)" : "var(--t3)", flex: "none" }),
  sub: { marginLeft: "auto", fontSize: 11, color: "var(--t3)", flex: "none" } as React.CSSProperties,
};

function meetingSub(m: MeetingMock): string {
  if (m.status === "live") return "live";
  const raw = String(m.live_status ?? "");
  if (["completed", "stopped", "failed"].includes(raw)) return "held";
  return m.when || "";
}

export function MinutesShell() {
  const meetings = useLiveMeetings();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [sel, setSel] = useState<Sel>({ kind: "personal", id: "personal", label: "Personal" });
  const [docPath, setDocPath] = useState<string>("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  const [docBody, setDocBody] = useState<string | null>(null);
  const [pages, setPages] = useState<{ path: string; slug?: string; label: string }[]>([]);

  useEffect(() => { void listSharedMemberships().then(setMemberships).catch(() => undefined); }, []);

  // ── selection → mounts + page set. One write room per conversation: selecting a shared room
  //    mounts it and parks the other shared mounts (the additive set stays a power feature).
  const select = useCallback(async (s: Sel) => {
    setSel(s); setDocBody(null);
    try {
      if (s.kind === "shared") {
        await setSharedActive(s.id, true);
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared" && m.slug !== s.id) { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      } else if (s.kind === "personal" || s.kind === "meeting") {
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared") { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      }
    } catch { /* mount changes are best-effort in the shell; the chat still runs */ }
    // page set per flavor
    if (s.kind === "shared") { setPages([{ path: "README.md", slug: s.id, label: "This room's page" }]); setDocPath("README.md"); setDocSlug(s.id); }
    else if (s.kind === "meeting") {
      const m = meetings.find((x) => x.id === s.id);
      const native = (m as { native_id?: string } | undefined)?.native_id;
      const art = native ? `kg/entities/meeting/${native}.md` : null;
      const tr = native ? `kg/entities/meeting/${native}.transcript.md` : null;
      const p = [ ...(art ? [{ path: art, label: "Minutes" }] : []), ...(tr ? [{ path: tr, label: "Transcript" }] : []), { path: "README.md", label: "Personal page" } ];
      setPages(p); setDocPath(p[0].path); setDocSlug(undefined);
    } else { setPages([{ path: "README.md", label: "This room's page" }]); setDocPath("README.md"); setDocSlug(undefined); }
  }, [meetings]);

  // page load (and reload on selection)
  useEffect(() => {
    let dead = false;
    setDocBody(null);
    readWorkspaceFile(docPath, docSlug ? { slug: docSlug } : undefined)
      .then((c) => { if (!dead) setDocBody(c); })
      .catch(() => { if (!dead) setDocBody(null); });
    return () => { dead = true; };
  }, [docPath, docSlug, sel.id]);

  // conversation id per room — real threads: the unit is agent-<uid>-chat-<session>
  const session = useMemo(() => {
    if (sel.kind === "personal") return "main";
    if (sel.kind === "shared") return `room-${sel.id.replace(/[^a-zA-Z0-9-]/g, "").slice(0, 24)}`;
    if (sel.kind === "org") return "org-setup";
    return `meet-${sel.id}`;
  }, [sel]);

  const flavor = sel.kind === "meeting" ? (meetingSub(meetings.find((m) => m.id === sel.id) ?? ({} as MeetingMock)) === "held" ? "meeting · held" : "meeting")
    : sel.kind === "shared" ? "room · shared" : sel.kind === "org" ? "room · admin" : "room · yours";
  const mounts = sel.kind === "shared" ? `[_global · ${sel.label} · _system]`
    : sel.kind === "org" ? "[_global rw · _system]"
    : sel.kind === "meeting" ? "[_global · personal · _system] + meeting" : "[_global · personal · _system]";

  const sorted = [...meetings].sort((a, b) => String(b.start_time ?? "").localeCompare(String(a.start_time ?? "")));

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, background: "var(--bg)" }}>
      {/* ── rail: two lenses over one set ── */}
      <nav style={S.rail} aria-label="Rooms and meetings">
        <div>
          <h2 style={S.h2}>Meetings</h2>
          {sorted.length === 0 && <div style={{ padding: "2px 8px", fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>They arrive by invitation.</div>}
          {sorted.map((m) => {
            const on = sel.kind === "meeting" && sel.id === m.id;
            return (
              <button key={m.id} style={S.row(on)} onClick={() => void select({ kind: "meeting", id: m.id, label: m.title.split(" — ")[0] })}>
                <span style={S.dot(on)} />{m.title.split(" — ")[0]}<span style={S.sub}>{meetingSub(m)}</span>
              </button>
            );
          })}
        </div>
        <div>
          <h2 style={S.h2}>Rooms</h2>
          {(["personal"] as const).map(() => {
            const on = sel.kind === "personal";
            return <button key="personal" style={S.row(on)} onClick={() => void select({ kind: "personal", id: "personal", label: "Personal" })}><span style={S.dot(on)} />Personal</button>;
          })}
          {memberships.map((m) => {
            const on = sel.kind === "shared" && sel.id === m.workspace_id;
            return (
              <button key={m.workspace_id} style={S.row(on)} onClick={() => void select({ kind: "shared", id: m.workspace_id, label: m.workspace_id })}>
                <span style={S.dot(on)} />{m.workspace_id}<span style={S.sub}>{m.role}</span>
              </button>
            );
          })}
          <button style={S.row(sel.kind === "org")} onClick={() => void select({ kind: "org", id: "org", label: "Organisation" })}>
            <span style={S.dot(sel.kind === "org")} />Organisation<span style={S.sub}>admin</span>
          </button>
        </div>
        <div style={{ marginTop: "auto", fontSize: 11.5, color: "var(--t3)", padding: 8, lineHeight: 1.55, borderTop: "1px solid var(--line2)" }}>
          Everything here is a room: a page you can read, a conversation you can have. Meetings are rooms that happened.
        </div>
      </nav>

      {/* ── center: the room's conversation ── */}
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 18px", borderBottom: "1px solid var(--line2)", background: "var(--sidebar)" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green, #5da86a)", flex: "none" }} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>{sel.label}</span>
          <span style={{ fontSize: 10.5, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--accent)", background: "var(--panel2)", borderRadius: 5, padding: "2px 8px", fontWeight: 600 }}>{flavor}</span>
          <span style={{ marginLeft: "auto", fontFamily: "monospace", fontSize: 11.5, color: "var(--t3)" }}>{mounts}</span>
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <Chat params={{ session }} />
        </div>
      </main>

      {/* ── right: the room's pages ── */}
      <aside style={{ width: 390, flex: "none", borderLeft: "1px solid var(--line2)", background: "var(--sidebar)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line2)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 10.5, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)", fontWeight: 600, marginRight: 4 }}>This room's pages</span>
          {pages.map((p) => (
            <button key={p.path} onClick={() => { setDocPath(p.path); setDocSlug(p.slug); }}
              style={{ font: "500 12px inherit", color: docPath === p.path ? "var(--accent)" : "var(--t2)", background: "var(--panel2)", border: `1px solid ${docPath === p.path ? "var(--accent)" : "var(--line2)"}`, borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
              {p.label}
            </button>
          ))}
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "18px 20px 40px" }}>
          {docBody === null
            ? <div style={{ fontSize: 13, color: "var(--t3)", lineHeight: 1.6 }}>No page here yet — it appears when the conversation (or a meeting) writes one.</div>
            : <MdxDoc>{docBody}</MdxDoc>}
        </div>
      </aside>
    </div>
  );
}
