"use client";
/** The rail: MEETINGS | PROJECTS — one segmented MODE switcher.
 *  Meetings = the agenda (Live · Upcoming · Past). Projects = your private bundles of
 *  workspaces, each with its chats; the workspace inventory (the shared folders) beneath. */
import type { CSSProperties } from "react";
import type { MeetingMock } from "../surfaces/meetingModel";
import type { Membership } from "../surfaces/workspaceApi";
import type { Project } from "./projects";
import type { Sel, View } from "./types";
import { T, row, surface, type as ty } from "./tokens";

const TERMINAL = new Set(["completed", "stopped", "failed"]);
export const isHeld = (m: MeetingMock) => TERMINAL.has(String((m as { live_status?: string }).live_status ?? ""));

function whenShort(m: MeetingMock): string {
  const t = (m as { start_time?: string }).start_time;
  if (!t) return m.status === "live" ? "live" : "";
  try {
    const d = new Date(t);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
      + (isHeld(m) ? "" : " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
  } catch { return ""; }
}

const seg: CSSProperties = { display: "flex", background: surface.raised, border: "1px solid var(--line)", borderRadius: 9, padding: 3, gap: 2, width: "100%" };
const segBtn = (on: boolean): CSSProperties => ({ ...ty.control, flex: 1, color: on ? "var(--t1)" : "var(--t3)", background: on ? surface.raisedHi : "transparent", border: "none", borderRadius: 7, padding: "5px 0", cursor: "pointer", boxShadow: on ? "0 1px 2px rgba(0,0,0,.28)" : "none" });
const meetRowS = (on: boolean): CSSProperties => ({ fontFamily: "var(--sans)", display: "flex", flexDirection: "column", gap: 2, padding: "7px 10px", borderRadius: 8, color: on ? "var(--t1)" : "var(--t2)", cursor: "pointer", background: on ? surface.raised : "transparent", border: "none", borderLeft: `2px solid ${on ? "var(--accent)" : "transparent"}`, width: "100%", textAlign: "left" });
const lensRow: CSSProperties = { ...ty.lens, display: "flex", alignItems: "center", padding: `2px ${T.rowPadX}px 4px` };
const projHead: CSSProperties = { ...ty.bodyStrong, display: "flex", alignItems: "center", gap: 7, padding: "4px 8px 3px", color: "var(--t1)" };
const wsRow: CSSProperties = { ...ty.chip, display: "flex", alignItems: "baseline", gap: 8, padding: "3px 8px", color: "var(--t2)" };

export function Rail(p: {
  view: View; onView: (v: View) => void;
  meetings: MeetingMock[]; memberships: Membership[]; projects: Project[];
  sel: Sel; onSelect: (s: Sel) => void;
  onNewChat: (projectId: string) => void; onNewProject: () => void; onNewWorkspace: () => void;
  collapsed: Record<string, boolean>; onToggleCollapse: (projectId: string) => void;
  onDeleteChat: (projectId: string, chatId: string) => void; onDeleteProject: (projectId: string) => void;
}) {
  const { view, sel } = p;
  const meetRow = (m: MeetingMock) => {
    const on = sel.kind === "meeting" && sel.id === m.id;
    return (
      <button key={m.id} style={meetRowS(on)} onClick={() => p.onSelect({ kind: "meeting", id: m.id, label: m.title.split(" — ")[0] })}>
        <span style={{ display: "flex", alignItems: "baseline", gap: 10, width: "100%" }}>
          <span style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", ...(on ? ty.bodyStrong : ty.body) }}>{m.title.split(" — ")[0]}</span>
          <span style={{ ...ty.meta, flex: "none", fontVariantNumeric: "tabular-nums" }}>{whenShort(m)}</span>
        </span>
        <span style={ty.meta}>Personal</span>
      </button>
    );
  };
  const chatRow = (on: boolean, label: string, sub: string | null, onClick: () => void, key: string, onDelete?: () => void) => (
    <div key={key} style={{ position: "relative", display: "flex" }}
      onMouseEnter={(e) => { const x = e.currentTarget.querySelector("[data-del]") as HTMLElement | null; if (x) x.style.opacity = "1"; }}
      onMouseLeave={(e) => { const x = e.currentTarget.querySelector("[data-del]") as HTMLElement | null; if (x) x.style.opacity = "0"; }}>
      <button style={{ ...row.base(on), paddingLeft: 20, paddingRight: onDelete ? 24 : 8 }} onClick={onClick}>
        <span style={row.dot(on)} />
        <span style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
        {sub && <span style={{ ...ty.meta, marginLeft: "auto", flex: "none" }}>{sub}</span>}
      </button>
      {onDelete && (
        <button data-del aria-label={`Delete ${label}`} title="Delete chat" onClick={(e) => { e.stopPropagation(); onDelete(); }}
          style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)", opacity: 0, transition: "opacity .12s", background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: "2px 4px", fontFamily: "var(--sans)" }}>×</button>
      )}
    </div>
  );

  const live = p.meetings.filter((m) => m.status === "live");
  const upcoming = p.meetings.filter((m) => !isHeld(m) && m.status !== "live").sort((a, b) => String((a as { start_time?: string }).start_time ?? "").localeCompare(String((b as { start_time?: string }).start_time ?? "")));
  const past = p.meetings.filter(isHeld).sort((a, b) => String((b as { start_time?: string }).start_time ?? "").localeCompare(String((a as { start_time?: string }).start_time ?? "")));

  return (
    <nav style={{ gridRow: "1 / 3", gridColumn: 1, borderRight: "1px solid var(--line)", background: surface.rail, display: "flex", flexDirection: "column", minHeight: 0 }} aria-label="Meetings and projects">
      <div style={{ height: T.headerH, flex: "none", display: "flex", alignItems: "center", padding: "0 10px", borderBottom: "1px solid var(--line)" }}>
        <div style={seg} role="tablist">
          <button style={segBtn(view === "meetings")} aria-pressed={view === "meetings"} onClick={() => p.onView("meetings")}>Meetings</button>
          <button style={segBtn(view === "projects")} aria-pressed={view === "projects"} onClick={() => p.onView("projects")}>Projects</button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "12px 10px", display: "flex", flexDirection: "column", gap: 4 }}>
        {view === "meetings" ? (<>
          {live.length > 0 && <><h2 style={lensRow}>Live</h2>{live.map(meetRow)}</>}
          {upcoming.length > 0 && <><h2 style={lensRow}>Upcoming</h2>{upcoming.map(meetRow)}</>}
          {past.length > 0 && <><h2 style={{ ...lensRow, marginTop: 12 }}>Past</h2>{past.map(meetRow)}</>}
          {p.meetings.length === 0 && <div style={{ ...ty.chip, padding: "2px 8px", color: "var(--t3)", lineHeight: 1.5 }}>They arrive by invitation.</div>}
        </>) : (<>
          <h2 style={lensRow}>Projects<button title="New project — pick its workspaces" aria-label="New project" onClick={p.onNewProject} style={row.ghostPlus}>+</button></h2>
          {p.projects.map((proj) => (
            <div key={proj.id} style={{ marginBottom: 10 }}>
              <div style={projHead} title={`[${proj.set.join(" · ")}]`}>
                <button aria-label={`${p.collapsed[proj.id] ? "Expand" : "Collapse"} ${proj.name}`} onClick={() => p.onToggleCollapse(proj.id)}
                  style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", padding: 0, width: 12, fontSize: 10, lineHeight: 1, fontFamily: "var(--sans)", transform: p.collapsed[proj.id] ? "rotate(-90deg)" : "none", transition: "transform .12s" }}>▾</button>
                <span style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{proj.name}</span>
                {proj.builtin === "org" && <span style={{ ...ty.meta }}>admin</span>}
                {!proj.builtin && proj.chats.length === 0 && (
                  <button title="Delete project (no chats)" aria-label={`Delete project ${proj.name}`} onClick={() => p.onDeleteProject(proj.id)}
                    style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: "0 2px", fontFamily: "var(--sans)" }}>×</button>
                )}
                {proj.builtin !== "org" && <button title={`New chat in ${proj.name}`} aria-label={`New chat in ${proj.name}`} onClick={() => p.onNewChat(proj.id)} style={row.ghostPlus}>+</button>}
              </div>
              {!p.collapsed[proj.id] && (<>
              {proj.builtin === "personal" && chatRow(sel.kind === "personal" && !sel.session, "main", null, () => p.onSelect({ kind: "personal", id: "personal", label: "Personal" }), "main")}
              {proj.builtin === "org" && chatRow(sel.kind === "org", "setup", null, () => p.onSelect({ kind: "org", id: "org", label: "Organisation" }), "org")}
              {proj.chats.map((c) => chatRow(sel.session === c.id, c.label, null,
                () => p.onSelect({ kind: "project", id: proj.id, label: proj.name, session: c.id, chatLabel: c.label }), c.id,
                () => p.onDeleteChat(proj.id, c.id)))}
              {proj.builtin === "personal" && p.meetings.map((m) => chatRow(sel.kind === "meeting" && sel.id === m.id, m.title.split(" — ")[0], isHeld(m) ? "held" : "upcoming",
                () => p.onSelect({ kind: "meeting", id: m.id, label: m.title.split(" — ")[0] }), `m-${m.id}`))}
              </>)}
            </div>
          ))}
          <h2 style={{ ...lensRow, marginTop: 14 }}>Workspaces<button title="New workspace — a conversation scaffolds it" aria-label="New workspace" onClick={p.onNewWorkspace} style={row.ghostPlus}>+</button></h2>
          <div style={wsRow}>personal<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>you</span></div>
          {p.memberships.map((m) => (
            <div key={"ws-" + m.workspace_id} style={wsRow}>{m.workspace_id}<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>{m.role}</span></div>
          ))}
          <div style={wsRow}>_global<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>everyone · ro</span></div>
        </>)}
      </div>

      <div style={{ ...ty.meta, flex: "none", padding: "10px", lineHeight: 1.55, borderTop: "1px solid var(--line)" }}>
        {view === "meetings" ? "Each meeting is a chat in your Personal project." : "A workspace is a folder — the shared thing. A project is your private bundle of workspaces to chat over."}
      </div>
    </nav>
  );
}
