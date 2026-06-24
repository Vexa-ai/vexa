/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * Foundation: Chat is the first real contribution; the others register as placeholders so the activity
 * bar is complete and additivity is visible. Each graduates to its own `@vexa/term-surface-*` module +
 * real views in its MVP (Workspace MVP1 · Routines/Tasks MVP2 · Inbox/Calendar MVP3 · Live MVP4 · …).
 */
import type { CSSProperties } from "react";
import { registerSurface, type SurfaceId } from "../contributions";

const wrap: CSSProperties = { maxWidth: 760, margin: "0 auto", padding: "40px 24px", color: "var(--t2)" };

function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div style={wrap}>
      <div style={{ fontSize: 15, color: "var(--t1)", fontWeight: 500, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 13, color: "var(--t3)" }}>{note}</div>
    </div>
  );
}

function ChatMain() {
  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: "22px 24px" }}>
      <div style={{ display: "flex", gap: 11, marginBottom: 18 }}>
        <div style={{ width: 26, height: 26, borderRadius: "50%", background: "var(--accentbg)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flex: "none" }}>V</div>
        <div style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12, padding: "10px 13px", maxWidth: "80%", fontSize: 13.5, color: "var(--t1)" }}>
          Workbench online. The chat unit comes alive in MVP0 — this composer will spawn an agent unit
          over your per-person git workspace and stream tools + a commit badge.
        </div>
      </div>
    </div>
  );
}

// ── the real first surface ─────────────────────────────────────────────────────
registerSurface({
  id: "chat",
  activity: { id: "chat", label: "Chat", icon: "msg", order: 20 },
  views: [{ id: "chat.main", slot: "main", component: ChatMain }],
  composer: { enabled: true, placeholder: "Type / for skills, or ask the agent…", quickChips: ["/research", "/draft", "/routine"] },
  commands: [
    { id: "routine.create", title: "Save what you did as a routine", skill: "/routine", run: () => { /* MVP2 */ } },
    { id: "research", title: "Research and file to the workspace", skill: "/research", run: () => {} },
    { id: "draft", title: "Draft an email or doc", skill: "/draft", run: () => {} },
  ],
});

// ── placeholder surfaces (each graduates to a real module in its MVP) ───────────
const PLACEHOLDERS: { id: SurfaceId; label: string; icon: string; order: number; live?: boolean; note: string }[] = [
  { id: "live", label: "Live meeting", icon: "radio", order: 10, live: true, note: "Real-time proactive cards + entity cockpit — MVP4 (the live-stream unit)." },
  { id: "workspace", label: "Workspace", icon: "panel", order: 30, note: "Git-backed knowledge graph (people/companies/meetings) — MVP1." },
  { id: "inbox", label: "Inbox", icon: "mail", order: 40, note: "Email + the Inbox-triage routine's proposed actions — MVP3." },
  { id: "calendar", label: "Calendar", icon: "cal", order: 50, note: "Calendar = meetings; past events are recorded meeting notes — MVP3." },
  { id: "tasks", label: "Tasks", icon: "tasks", order: 60, note: "Action items from meetings, email & routines — MVP2." },
  { id: "routines", label: "Routines", icon: "zap", order: 70, note: "Trigger → plan; create from chat with /routine — MVP2." },
];
for (const p of PLACEHOLDERS) {
  registerSurface({
    id: p.id,
    activity: { id: p.id, label: p.label, icon: p.icon, order: p.order, live: p.live },
    views: [{ id: `${p.id}.main`, slot: "main", component: () => <Placeholder title={p.label} note={p.note} /> }],
  });
}
