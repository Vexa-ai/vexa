"use client";
/** MINUTES — the one-object shell (#1311), design source: biz drafts/minutes-mock-chat.
 *
 *  WORKSPACES hold knowledge · a ROOM is a set of workspaces · CHATS live in rooms and inherit
 *  the set. The rail opens on a segmented MEETINGS | ROOMS switcher that is a MODE: it swaps the
 *  whole context (conversation + pages + header), remembering each view's last selection.
 *  Meetings is the agenda — Upcoming then Past, two-line rows (name + time / room). Rooms is the
 *  structure — rooms with their chats, and the workspace inventory beneath.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import {
  listSharedMemberships, readActiveSet, setSharedActive, deactivateWorkspace,
  readWorkspaceFile, type Membership,
} from "../surfaces/workspaceApi";
import { MdxDoc } from "../ui-kit/MdxDoc";

type Sel = { kind: "personal" | "shared" | "org" | "meeting"; id: string; label: string };
type View = "meetings" | "rooms";

const TERMINAL = new Set(["completed", "stopped", "failed"]);
const isHeld = (m: MeetingMock) => TERMINAL.has(String((m as { live_status?: string }).live_status ?? ""));
function whenShort(m: MeetingMock): string {
  const t = (m as { start_time?: string }).start_time;
  if (!t) return m.status === "live" ? "live" : "";
  try {
    const d = new Date(t);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
      + (isHeld(m) ? "" : " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
  } catch { return ""; }
}

const S = {
  rail: { width: 252, flex: "none", borderRight: "1px solid var(--line2)", background: "var(--sidebar)", padding: "16px 10px", display: "flex", flexDirection: "column", gap: 4, overflowY: "auto" } as React.CSSProperties,
  lens: { fontSize: 10.5, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)", padding: "2px 8px 4px", fontWeight: 600, display: "flex", alignItems: "center" } as React.CSSProperties,
  seg: { display: "flex", background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 9, padding: 3, margin: "0 6px 12px", gap: 2 } as React.CSSProperties,
  segBtn: (on: boolean): React.CSSProperties => ({ flex: 1, font: "600 12.5px inherit", fontFamily: "inherit", color: on ? "var(--t1)" : "var(--t3)", background: on ? "var(--panel2)" : "transparent", border: "none", borderRadius: 7, padding: "6px 0", cursor: "pointer", boxShadow: on ? "0 1px 2px rgba(0,0,0,.25)" : "none" }),
  meetrow: (on: boolean): React.CSSProperties => ({ display: "flex", flexDirection: "column", gap: 2, padding: "7px 10px", borderRadius: 8, color: on ? "var(--t1)" : "var(--t2)", cursor: "pointer", background: on ? "var(--panel2)" : "transparent", border: "none", borderLeft: `2px solid ${on ? "var(--accent)" : "transparent"}`, width: "100%", textAlign: "left", fontFamily: "inherit" }),
  r1: { display: "flex", alignItems: "baseline", gap: 10, width: "100%" } as React.CSSProperties,
  name: (on: boolean): React.CSSProperties => ({ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontSize: 13, fontWeight: on ? 600 : 500 }),
  when: { flex: "none", fontSize: 11, color: "var(--t3)", fontVariantNumeric: "tabular-nums" } as React.CSSProperties,
  sub: { fontSize: 11, color: "var(--t3)" } as React.CSSProperties,
  chatrow: (on: boolean): React.CSSProperties => ({ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px 4px 20px", borderRadius: 7, color: on ? "var(--t1)" : "var(--t2)", cursor: "pointer", fontSize: 13, background: on ? "var(--panel2)" : "transparent", border: "none", width: "100%", textAlign: "left", fontFamily: "inherit" }),
  dot: (on: boolean): React.CSSProperties => ({ width: 5, height: 5, borderRadius: "50%", background: on ? "var(--accent)" : "var(--line2)", flex: "none" }),
  roomhead: { display: "flex", alignItems: "center", gap: 7, padding: "4px 8px 3px", font: "600 13px inherit", fontFamily: "inherit", color: "var(--t1)" } as React.CSSProperties,
};

export function MinutesShell() {
  const meetings = useLiveMeetings();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [view, setView] = useState<View>("meetings");
  const [sel, setSel] = useState<Sel>({ kind: "personal", id: "personal", label: "Personal" });
  const lastSel = useRef<{ meetings: Sel | null; rooms: Sel | null }>({ meetings: null, rooms: { kind: "personal", id: "personal", label: "Personal" } });
  const [docPath, setDocPath] = useState<string>("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  const [docBody, setDocBody] = useState<string | null>(null);
  const [pages, setPages] = useState<{ path: string; slug?: string; label: string }[]>([]);

  useEffect(() => { void listSharedMemberships().then(setMemberships).catch(() => undefined); }, []);

  const select = useCallback(async (s: Sel) => {
    setSel(s); setDocBody(null);
    lastSel.current[s.kind === "meeting" ? "meetings" : "rooms"] = s;
    try {
      if (s.kind === "shared") {
        await setSharedActive(s.id, true);
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared" && m.slug !== s.id) { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      } else if (s.kind === "personal" || s.kind === "meeting") {
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared") { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      }
    } catch { /* best-effort; the chat still runs */ }
    if (s.kind === "shared") { setPages([{ path: "README.md", slug: s.id, label: "This room's page" }]); setDocPath("README.md"); setDocSlug(s.id); }
    else if (s.kind === "meeting") {
      const m = meetings.find((x) => x.id === s.id);
      const native = (m as { native_id?: string } | undefined)?.native_id;
      const p = [ ...(native ? [{ path: `kg/entities/meeting/${native}.md`, label: "Minutes" }, { path: `kg/entities/meeting/${native}.transcript.md`, label: "Transcript" }] : []), { path: "README.md", label: "Personal page" } ];
      setPages(p); setDocPath(p[0].path); setDocSlug(undefined);
    } else { setPages([{ path: "README.md", label: "This room's page" }]); setDocPath("README.md"); setDocSlug(undefined); }
  }, [meetings]);

  // MODE switch: swap the whole context, restoring the view's last selection
  const switchView = useCallback((v: View) => {
    if (v === view) return;
    setView(v);
    const want = lastSel.current[v];
    if (want) { void select(want); return; }
    if (v === "meetings") {
      const first = [...meetings].sort((a, b) => String(b.start_time ?? "").localeCompare(String(a.start_time ?? "")))[0];
      if (first) void select({ kind: "meeting", id: first.id, label: first.title.split(" — ")[0] });
    } else void select({ kind: "personal", id: "personal", label: "Personal" });
  }, [view, meetings, select]);

  useEffect(() => {
    let dead = false;
    setDocBody(null);
    readWorkspaceFile(docPath, docSlug ? { slug: docSlug } : undefined)
      .then((c) => { if (!dead) setDocBody(c); })
      .catch(() => { if (!dead) setDocBody(null); });
    return () => { dead = true; };
  }, [docPath, docSlug, sel.id]);

  const session = useMemo(() => {
    if (sel.kind === "personal") return "main";
    if (sel.kind === "shared") return `room-${sel.id.replace(/[^a-zA-Z0-9-]/g, "").slice(0, 24)}`;
    if (sel.kind === "org") return "org-setup";
    return `meet-${sel.id}`;
  }, [sel]);

  const flavor = sel.kind === "meeting" ? `meeting · ${isHeld(meetings.find((m) => m.id === sel.id) ?? ({} as MeetingMock)) ? "held" : "upcoming"}`
    : sel.kind === "shared" ? "room · shared" : sel.kind === "org" ? "room · admin" : "room · yours";
  const mounts = sel.kind === "shared" ? `[_global · ${sel.label} · _system]`
    : sel.kind === "org" ? "[_global rw · _system]"
    : sel.kind === "meeting" ? "[_global · personal · _system] + meeting" : "[_global · personal · _system]";

  const upcoming = meetings.filter((m) => !isHeld(m) && m.status !== "live").sort((a, b) => String(a.start_time ?? "").localeCompare(String(b.start_time ?? "")));
  const live = meetings.filter((m) => m.status === "live");
  const past = meetings.filter(isHeld).sort((a, b) => String(b.start_time ?? "").localeCompare(String(a.start_time ?? "")));

  const meetRow = (m: MeetingMock) => {
    const on = sel.kind === "meeting" && sel.id === m.id;
    return (
      <button key={m.id} style={S.meetrow(on)} onClick={() => void select({ kind: "meeting", id: m.id, label: m.title.split(" — ")[0] })}>
        <span style={S.r1}><span style={S.name(on)}>{m.title.split(" — ")[0]}</span><span style={S.when}>{whenShort(m)}</span></span>
        <span style={S.sub}>Personal</span>
      </button>
    );
  };

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, background: "var(--bg)" }}>
      <nav style={S.rail} aria-label="Meetings and rooms">
        <div style={S.seg} role="tablist">
          <button style={S.segBtn(view === "meetings")} aria-pressed={view === "meetings"} onClick={() => switchView("meetings")}>Meetings</button>
          <button style={S.segBtn(view === "rooms")} aria-pressed={view === "rooms"} onClick={() => switchView("rooms")}>Rooms</button>
        </div>

        {view === "meetings" ? (<>
          {live.length > 0 && <><h2 style={S.lens}>Live</h2>{live.map(meetRow)}</>}
          {upcoming.length > 0 && <><h2 style={S.lens}>Upcoming</h2>{upcoming.map(meetRow)}</>}
          {past.length > 0 && <><h2 style={{ ...S.lens, marginTop: 12 }}>Past</h2>{past.map(meetRow)}</>}
          {meetings.length === 0 && <div style={{ padding: "2px 8px", fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>They arrive by invitation.</div>}
        </>) : (<>
          <div style={{ marginBottom: 10 }}>
            <div style={S.roomhead}>Personal</div>
            <button style={S.chatrow(sel.kind === "personal")} onClick={() => void select({ kind: "personal", id: "personal", label: "Personal" })}>
              <span style={S.dot(sel.kind === "personal")} />main
            </button>
          </div>
          {memberships.map((m) => {
            const on = sel.kind === "shared" && sel.id === m.workspace_id;
            return (
              <div key={m.workspace_id} style={{ marginBottom: 10 }}>
                <div style={S.roomhead}>{m.workspace_id}<span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--t3)", fontWeight: 400 }}>{m.role}</span></div>
                <button style={S.chatrow(on)} onClick={() => void select({ kind: "shared", id: m.workspace_id, label: m.workspace_id })}>
                  <span style={S.dot(on)} />group thread
                </button>
              </div>
            );
          })}
          <div style={{ marginBottom: 10 }}>
            <div style={S.roomhead}>Organisation<span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--t3)", fontWeight: 400 }}>admin</span></div>
            <button style={S.chatrow(sel.kind === "org")} onClick={() => void select({ kind: "org", id: "org", label: "Organisation" })}>
              <span style={S.dot(sel.kind === "org")} />setup
            </button>
          </div>
        </>)}

        <div style={{ marginTop: "auto", fontSize: 11.5, color: "var(--t3)", padding: 8, lineHeight: 1.55, borderTop: "1px solid var(--line2)" }}>
          {view === "meetings"
            ? "Everything you're invited to, and everything held — each meeting is a chat in the room that owns it."
            : "A room is a set of workspaces; its chats see that set."}
        </div>
      </nav>

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

      <aside style={{ width: 390, flex: "none", borderLeft: "1px solid var(--line2)", background: "var(--sidebar)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line2)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 10.5, letterSpacing: ".09em", textTransform: "uppercase", color: "var(--t3)", fontWeight: 600, marginRight: 4 }}>This room's pages</span>
          {pages.map((p) => (
            <button key={p.path} onClick={() => { setDocPath(p.path); setDocSlug(p.slug); }}
              style={{ font: "500 12px inherit", fontFamily: "inherit", color: docPath === p.path ? "var(--accent)" : "var(--t2)", background: "var(--panel2)", border: `1px solid ${docPath === p.path ? "var(--accent)" : "var(--line2)"}`, borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
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
