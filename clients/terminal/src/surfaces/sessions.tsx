"use client";
/** Sessions — the chat-sessions LIST (left). "New session" opens a fresh Chat tab; a saved session opens
 *  its Chat tab (resume). Lists from /api/sessions. (Multi-session backend wiring is a later increment.) */
import { useEffect, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList } from "../contributions";
import { Icon } from "../ui-kit";

const SUBJECT = "u_jane";

function SessionsList() {
  const layout = useService(LayoutServiceId);
  const [sessions, setSessions] = useState<string[]>([]);
  useEffect(() => { void (async () => { try { setSessions((await (await fetch(`/api/sessions?subject=${SUBJECT}`)).json()).sessions ?? []); } catch { /* offline */ } })(); }, []);

  const newSession = () => layout.openTab({ id: `chat:${Date.now().toString(36)}`, title: "New chat", kind: "chat", params: { subject: SUBJECT, session: null }, context: null });
  const open = (s: string) => layout.openTab({ id: `chat:${s}`, title: s.slice(0, 8), kind: "chat", params: { subject: SUBJECT, session: s }, context: null });

  return (
    <div style={{ padding: "8px" }}>
      <button onClick={newSession} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
        <Icon name="plus" size={14} />New session
      </button>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>sessions</div>
      {sessions.map((s) => (
        <div key={s} onClick={() => open(s)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <Icon name="msg" size={13} />{s.slice(0, 8)}…
        </div>
      ))}
      {sessions.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No saved sessions yet — start one above.</div>}
    </div>
  );
}

registerList({ id: "sessions", label: "Sessions", icon: "msg", order: 10, component: SessionsList });
