"use client";
/** Sessions — the chat-sessions LIST (left). The chat itself is the persistent right rail. */
import { useEffect, useState } from "react";
import { useService, useStore } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList } from "../contributions";
import { Icon } from "../ui-kit";

const SUBJECT = "u_live";  // the terminal's single subject (matches workspace + meetings; one identity until §0 auth)

export interface SessionSummary {
  session: string;
  title?: string | null;
  created?: string | null;
  last_active?: string | null;
}

const truncateSessionId = (session: string) => session.length > 18 ? `${session.slice(0, 18)}...` : session;

function meetingLabel(value: string): string {
  const meeting = (value.split("·").pop()?.trim() || value.trim()).replace(/^["'\\]+|["'\\.)]+$/g, "");
  return meeting ? `Meeting ${meeting}` : "Meeting";
}

function compactTitle(title: string): string {
  const raw = title.trim().replace(/^["']|["']$/g, "");
  const activeRef = raw.match(/^Active meeting reference:\s*@meeting:([A-Za-z0-9._~%+@:/=-]+)/);
  if (activeRef) return meetingLabel(activeRef[1]);
  const activeMeeting = raw.match(/^Active meeting ([A-Za-z0-9._~%+@:/=-]+)/);
  if (activeMeeting) return meetingLabel(activeMeeting[1]);
  const legacyCopilot = raw.match(/^You are the copilot for a live meeting \((?:\\)?["']([^"']+)/);
  if (legacyCopilot) return meetingLabel(legacyCopilot[1]);
  return raw;
}

export const sessionTitle = (s: SessionSummary) => s.title?.trim() ? compactTitle(s.title) : truncateSessionId(s.session);

function SessionsList() {
  const layout = useService(LayoutServiceId);
  const { activeSession } = useStore(layout.store);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => { void (async () => { try { const data = await (await fetch(`/api/sessions?subject=${SUBJECT}`)).json() as { sessions?: SessionSummary[] }; setSessions(data.sessions ?? []); } catch { /* offline */ } })(); }, []);

  const focusChat = () => {
    layout.showRight();
    window.setTimeout(() => window.dispatchEvent(new Event("vexa:terminal:focus-chat")), 0);
  };
  // switch the right rail to a session (revealing it) from the recent-session list.
  const openSession = (id: string) => { layout.setActiveSession(id); focusChat(); };

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {sessions.map((s) => (
        <SessionRow key={s.session} session={s} active={s.session === activeSession} onOpen={() => openSession(s.session)} />
      ))}
      {sessions.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No saved sessions yet.</div>}
    </div>
  );
}

function SessionRow({ session, active, onOpen }: { session: SessionSummary; active: boolean; onOpen: () => void }) {
  return (
    <div
      onClick={onOpen}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: active ? "var(--t1)" : "var(--t2)", background: active ? "var(--panel2)" : "transparent" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = active ? "var(--panel2)" : "transparent")}>
      <Icon name="msg" size={13} />{sessionTitle(session)}
    </div>
  );
}

registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
