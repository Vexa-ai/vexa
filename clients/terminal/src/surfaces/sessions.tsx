"use client";
/** Sessions — the chat-sessions LIST (left). The chat itself is the persistent right rail. */
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

const truncateSessionId = (session: string) => session.length > 18 ? `${session.slice(0, 18)}...` : session;
const sessionTitle = (s: SessionSummary) => s.title?.trim() || truncateSessionId(s.session);

function SessionsList() {
  const layout = useService(LayoutServiceId);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => { void (async () => { try { const data = await (await fetch(`/api/sessions?subject=${SUBJECT}`)).json() as { sessions?: SessionSummary[] }; setSessions(data.sessions ?? []); } catch { /* offline */ } })(); }, []);

  const focusChat = () => {
    layout.showRight();
    window.setTimeout(() => window.dispatchEvent(new Event("vexa:terminal:focus-chat")), 0);
  };

  return (
    <div style={{ padding: "8px" }}>
      <button onClick={focusChat} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
        <Icon name="msg" size={14} />Focus chat
      </button>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {sessions.map((s) => (
        <SessionRow key={s.session} session={s} onFocus={focusChat} />
      ))}
      {sessions.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No saved sessions yet — use the chat in the right rail.</div>}
    </div>
  );
}

function SessionRow({ session, onFocus }: { session: SessionSummary; onFocus: () => void }) {
  return (
    <div
      onClick={() => onFocus()}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <Icon name="msg" size={13} />{sessionTitle(session)}
    </div>
  );
}

registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
