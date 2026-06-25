"use client";
/** Sessions — the chat-sessions LIST (left). "New session" opens a fresh Chat tab; a saved session opens
 *  its Chat tab (resume). Lists from /api/sessions. */
import { useEffect, useRef, useState } from "react";
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

  const chatTab = (session: string, title: string) => ({ id: `chat:${session}`, title, kind: "chat", params: { subject: SUBJECT, session }, context: null });

  // "New session" is an explicit action → pinned tab.
  const newSession = () => { const session = mintSessionId(); layout.openTab(chatTab(session, "New chat")); };
  // single-click → preview, double-click → pinned (debounced so a dblclick leaves no stray preview).
  const previewSession = (s: SessionSummary) => layout.openPreview(chatTab(s.session, sessionTitle(s)));
  const pinSession = (s: SessionSummary) => layout.openTab(chatTab(s.session, sessionTitle(s)));

  return (
    <div style={{ padding: "8px" }}>
      <button onClick={newSession} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
        <Icon name="plus" size={14} />New session
      </button>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {sessions.map((s) => (
        <SessionRow key={s.session} session={s} onPreview={() => previewSession(s)} onPin={() => pinSession(s)} />
      ))}
      {sessions.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No saved sessions yet — start one above.</div>}
    </div>
  );
}

function SessionRow({ session, onPreview, onPin }: { session: SessionSummary; onPreview: () => void; onPin: () => void }) {
  return (
    <div
      onClick={() => onPreview()}
      onDoubleClick={() => onPin()}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <Icon name="msg" size={13} />{sessionTitle(session)}
    </div>
  );
}

registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
