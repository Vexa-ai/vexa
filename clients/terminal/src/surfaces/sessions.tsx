"use client";
/** Sessions — the chat-sessions LIST (left). "New session" opens a fresh Chat tab; a saved session opens
 *  its Chat tab (resume). Lists from /api/sessions. */
import { useEffect, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList } from "../contributions";
import { Icon } from "../ui-kit";

const SUBJECT = "u_live";  // the terminal's single subject (matches workspace + meetings; one identity until §0 auth)

interface SessionSummary {
  session: string;
  title?: string | null;
  created?: string | null;
  last_active?: string | null;
}

const mintSessionId = () => `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const truncateSessionId = (session: string) => session.length > 18 ? `${session.slice(0, 18)}...` : session;
const sessionTitle = (s: SessionSummary) => s.title?.trim() || truncateSessionId(s.session);

function SessionsList() {
  const layout = useService(LayoutServiceId);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => { void (async () => { try { const data = await (await fetch(`/api/sessions?subject=${SUBJECT}`)).json() as { sessions?: SessionSummary[] }; setSessions(data.sessions ?? []); } catch { /* offline */ } })(); }, []);

  const newSession = () => {
    const session = mintSessionId();
    layout.openTab({ id: `chat:${session}`, title: "New chat", kind: "chat", params: { subject: SUBJECT, session }, context: null });
  };
  const open = (s: SessionSummary) => layout.openTab({ id: `chat:${s.session}`, title: sessionTitle(s), kind: "chat", params: { subject: SUBJECT, session: s.session }, context: null });

  return (
    <div style={{ padding: "8px" }}>
      <button onClick={newSession} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
        <Icon name="plus" size={14} />New session
      </button>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {sessions.map((s) => (
        <div key={s.session} onClick={() => open(s)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <Icon name="msg" size={13} />{sessionTitle(s)}
        </div>
      ))}
      {sessions.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No saved sessions yet — start one above.</div>}
    </div>
  );
}

registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
