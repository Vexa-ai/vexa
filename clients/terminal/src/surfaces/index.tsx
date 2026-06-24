/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * Chat is the first real contribution (./chat, MVP0). The rest register as placeholders so the
 * activity bar is complete and additivity is visible; each graduates to its own module + real views
 * in its MVP (Workspace MVP1 · Routines/Tasks MVP2 · Inbox/Calendar MVP3 · Live MVP4 · …).
 */
import type { CSSProperties } from "react";
import { registerSurface, type SurfaceId } from "../contributions";
import "./chat";
import "./workspace";
import "./tasks";

const wrap: CSSProperties = { maxWidth: 760, margin: "0 auto", padding: "40px 24px" };

function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div style={wrap}>
      <div style={{ fontSize: 15, color: "var(--t1)", fontWeight: 500, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 13, color: "var(--t3)" }}>{note}</div>
    </div>
  );
}

const PLACEHOLDERS: { id: SurfaceId; label: string; icon: string; order: number; live?: boolean; note: string }[] = [
  { id: "live", label: "Live meeting", icon: "radio", order: 10, live: true, note: "Real-time proactive cards + entity cockpit — MVP4 (the live-stream unit)." },
  { id: "inbox", label: "Inbox", icon: "mail", order: 40, note: "Email + the Inbox-triage routine's proposed actions — MVP3." },
  { id: "calendar", label: "Calendar", icon: "cal", order: 50, note: "Calendar = meetings; past events are recorded meeting notes — MVP3." },
  { id: "routines", label: "Routines", icon: "zap", order: 70, note: "Trigger → plan; create from chat with /routine — MVP2." },
];
for (const p of PLACEHOLDERS) {
  registerSurface({
    id: p.id,
    activity: { id: p.id, label: p.label, icon: p.icon, order: p.order, live: p.live },
    views: [{ id: `${p.id}.main`, slot: "main", component: () => <Placeholder title={p.label} note={p.note} /> }],
  });
}
