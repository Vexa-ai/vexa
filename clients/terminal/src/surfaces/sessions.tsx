"use client";
/** Sessions — the chat-sessions LIST (left). The chat itself is the persistent right rail. */
import { useEffect, useState } from "react";
import { useService, useStore } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { Icon } from "../ui-kit";
// Data-access lives in its own SoC module (scoped to the authed user — no client subject, P20),
// proven in isolation by sessionsApi.test.ts.
import { listSessions, type SessionSummary } from "./sessionsApi";
import { ONBOARDING_KICKOFF_MARK } from "../canvas/actions";
export type { SessionSummary } from "./sessionsApi";  // re-exported for the chat surface

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

export const sessionTitle = (s: SessionSummary) => {
  const title = s.title?.trim();
  // A session titled by the onboarding kickoff (the first message) must not show the raw prompt — fall
  // back to the session id (e.g. "main").
  if (title && !title.includes(ONBOARDING_KICKOFF_MARK)) return compactTitle(title);
  return truncateSessionId(s.session);
};

function SessionsList() {
  const layout = useService(LayoutServiceId);
  const { activeSession } = useStore(layout.store);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18)
  useEffect(() => { void listSessions().then((s) => { setSessions(s); setError(null); }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))); }, []);

  const focusChat = () => {
    layout.showRight();
    window.setTimeout(() => window.dispatchEvent(new Event("vexa:terminal:focus-chat")), 0);
  };
  // switch the right rail to a session (revealing it) from the recent-session list.
  const openSession = (id: string) => { layout.setActiveSession(id); focusChat(); };

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "6px 9px" }}>⚠ Couldn’t load sessions — {error}</div>}
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

// Agent surface — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings).
if (!meetingsOnly()) {
  registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
}
