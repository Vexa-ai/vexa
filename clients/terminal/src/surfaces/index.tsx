/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * A surface is a self-contained module that `registerSurface`s its activity item + view(s); the
 * workbench shell never hardcodes screens (P2/P6 on the client). Real surfaces register first; the rest
 * are placeholders so the activity bar is complete and additivity is visible — each graduates in its MVP
 * (Chat MVP0 · Workspace MVP1 · Tasks/Routines MVP2 · Inbox/Calendar MVP3 · Live MVP4).
 */
import type { CSSProperties } from "react";
import { registerSurface, type SurfaceId } from "../contributions";
import "./chat";
import "./workspace";
import "./tasks";
import "./routines";

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
  { id: "inbox", label: "Inbox", icon: "mail", order: 40, note: "Email as a first-class terminal tool (reads the email tool/service, not a backend primitive) — MVP3." },
  { id: "calendar", label: "Calendar", icon: "cal", order: 50, note: "Calendar = meetings; past events are recorded meeting notes — MVP3." },
];
for (const p of PLACEHOLDERS) {
  registerSurface({
    id: p.id,
    activity: { id: p.id, label: p.label, icon: p.icon, order: p.order, live: p.live },
    views: [{ id: `${p.id}.main`, slot: "main", component: () => <Placeholder title={p.label} note={p.note} /> }],
  });
}
