"use client";
/** Meetings (mocked backend) — the differentiator flow, rendered through the shared agent-window.
 *  • "calendar" LIST (left): meetings; the live one auto-opens; click any to (re)open its copilot.
 *  • "meeting" TAB (center): ONE stacked agent window — entities strip on top · the copilot
 *    conversation (live insights + your turns, with the agent's operations visible) · ask input ·
 *    proposed actions directly under the input. No horizontal split.
 *  • "transcript" CONTEXT (right): the real-time transcript. */
import { useEffect, useRef, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { registerList, registerTab, registerContext, registerCommand, type TabProps, type ContextProps } from "../contributions";
import { Icon } from "../ui-kit";
import { MEETINGS, meetingById, liveMeeting, type MeetingMock } from "./mock";

export function meetingTab(m: MeetingMock): TabDescriptor {
  return { id: `meeting:${m.id}`, title: m.title.split(" — ")[0], kind: "meeting", params: { meetingId: m.id }, context: { kind: "transcript", params: { meetingId: m.id } } };
}

/** progressive reveal of a timeline (live = stream; past = all at once) */
function useReveal(n: number, live: boolean, stepMs = 3500): number {
  const [k, setK] = useState(live ? Math.min(2, n) : n);
  useEffect(() => {
    if (!live) { setK(n); return; }
    const id = setInterval(() => setK((c) => (c >= n ? c : c + 1)), stepMs);
    return () => clearInterval(id);
  }, [n, live, stepMs]);
  return k;
}

// ── Calendar LIST (left) ─────────────────────────────────────────────────────────
function CalendarList() {
  const layout = useService(LayoutServiceId);
  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>meetings</div>
      {MEETINGS.map((m) => (
        <div key={m.id} onClick={() => layout.openTab(meetingTab(m))} style={{ padding: "8px 9px", borderRadius: 7, cursor: "pointer", marginBottom: 2 }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            {m.status === "live" && <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", flex: "none" }} />}
            <span style={{ fontSize: 13, color: "var(--t1)", fontWeight: m.status === "live" ? 600 : 400 }}>{m.title}</span>
          </div>
          <div style={{ fontSize: 11.5, color: m.status === "live" ? "var(--live)" : "var(--t3)", marginTop: 2, paddingLeft: m.status === "live" ? 14 : 0 }}>{m.when} · {m.platform}</div>
        </div>
      ))}
    </div>
  );
}

// ── entity strip (top of the agent window) ────────────────────────────────────────
function EntitiesBar({ m }: { m: MeetingMock }) {
  const layout = useService(LayoutServiceId);
  const live = m.status === "live";
  const avatar = { width: 22, height: 22, borderRadius: "50%", background: "var(--panel2)", color: "var(--t1)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, flex: "none" } as const;
  return (
    <div style={{ flex: "none", borderBottom: "1px solid var(--line)", padding: "9px 16px", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      {live && <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--live)", flex: "none" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)" }} />LIVE · {m.platform}</span>}
      {m.participants.map((p) => (
        <span key={p.name} style={{ display: "inline-flex", alignItems: "center", gap: 6 }} title={p.role}><span style={avatar}>{p.initials}</span><span style={{ fontSize: 12.5, color: "var(--t1)" }}>{p.name}</span></span>
      ))}
      <span style={{ width: 1, height: 16, background: "var(--line)", flex: "none" }} />
      {m.mentioned.map((e) => (
        <span key={e} onClick={() => layout.openTab({ id: `entity:${e}`, title: e, kind: "chat", params: { subject: "u_jane", session: null }, context: null })}
          style={{ fontSize: 11.5, color: "var(--blue)", border: "1px solid var(--line2)", borderRadius: 20, padding: "2px 9px", cursor: "pointer", flex: "none" }}>{e}</span>
      ))}
    </div>
  );
}

// ── Meeting COPILOT tab (center) — the stacked agent window ────────────────────────
function MeetingTab({ params }: TabProps) {
  const m = meetingById(params.meetingId as string);
  const live = m?.status === "live";
  const shown = useReveal(m?.insights.length ?? 0, !!live);
  const [feed, setFeed] = useState<Turn[]>([]);
  const [value, setValue] = useState("");
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [shown, feed]);
  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;

  // insights stream as `insight` turns; the user's actions append `user` + `agent` turns below them
  const insightTurns: Turn[] = m.insights.slice(0, shown).map((i, idx) => ({ id: `ins-${idx}`, role: "insight", t: i.t, text: i.text }));
  const turns: Turn[] = [...insightTurns, ...feed];

  // run an action / ask: append a user turn + an agent turn whose operations resolve over ~2s (mock)
  const run = (label: string) => {
    const n = idRef.current++;
    const aid = `a-${n}`;
    const ops: Op[] = [
      { icon: opIcon.read, label: "read kg/entities/company/acme-corp.md", status: "running" },
      { icon: opIcon.search, label: 'search transcript · "renewal", "SSO"', status: "running" },
      { icon: opIcon.edit, label: "edit drafts/acme-renewal.md", status: "running" },
    ];
    setFeed((f) => [...f, { id: `u-${n}`, role: "user", text: label }, { id: aid, role: "agent", text: "", ops }]);
    const patch = (fn: (t: Extract<Turn, { role: "agent" }>) => Extract<Turn, { role: "agent" }>) =>
      setFeed((f) => f.map((t) => (t.id === aid && t.role === "agent" ? fn(t) : t)));
    ops.forEach((_, k) => setTimeout(() => patch((t) => ({ ...t, ops: t.ops.map((o, j) => (j <= k ? { ...o, status: "done" } : o)) })), 450 * (k + 1)));
    setTimeout(() => patch((t) => ({ ...t, text: `Done — ${label.toLowerCase()}. I committed the change to the workspace; it's ready to send on your approval.`, commit: "a1b2c3d4" })), 450 * (ops.length + 1));
  };

  const top = <EntitiesBar m={m} />;
  const composer = (
    <div style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "10px 12px", display: "flex", alignItems: "center", gap: 10 }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) { run(value.trim()); setValue(""); } }}
        placeholder="Ask the agent about this meeting…" style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
      <button aria-label="Send" onClick={() => { if (value.trim()) { run(value.trim()); setValue(""); } }} style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}><Icon name="send" size={16} /></button>
    </div>
  );
  const actions = (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 11, color: "var(--t3)", flex: "none" }}>proposed</span>
      {m.actions.map((a) => (
        <button key={a.id} onClick={() => run(a.label)} title={a.detail}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 20, background: "var(--panel)", color: "var(--t1)", padding: "5px 12px", fontSize: 12.5, cursor: "pointer" }}>
          <Icon name="zap" size={12} style={{ color: "var(--accent)" }} />{a.label}
        </button>
      ))}
    </div>
  );

  return (
    <AgentWindow top={top} scrollRef={scrollRef} composer={composer} actions={actions}>
      <div style={{ fontSize: 12, color: "var(--t3)", marginBottom: 14 }}>Meeting copilot — I follow the conversation and surface what helps. {live ? "Listening…" : "Recording from this meeting."}</div>
      <Conversation turns={turns} />
    </AgentWindow>
  );
}

// ── Transcript CONTEXT (right) ────────────────────────────────────────────────────
function TranscriptContext({ params }: ContextProps) {
  const m = meetingById(params.meetingId as string);
  const live = m?.status === "live";
  const shown = useReveal(m?.transcript.length ?? 0, !!live, 3000);
  if (!m) return <div style={{ padding: 16, color: "var(--t3)" }}>No transcript.</div>;
  return (
    <div style={{ padding: "14px 16px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}>
        {live && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--live)" }} />}transcript
      </div>
      {m.transcript.slice(0, shown).map((l, i) => (
        <div key={i} style={{ marginBottom: 11 }}>
          <div style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--t3)", marginBottom: 2 }}><span style={{ fontFamily: "var(--mono)" }}>{l.t}</span><span style={{ color: "var(--t2)", fontWeight: 500 }}>{l.speaker}</span></div>
          <div style={{ fontSize: 13, color: "var(--t1)", lineHeight: 1.5 }}>{l.text}</div>
        </div>
      ))}
      {live && shown < m.transcript.length && <div style={{ fontSize: 12, color: "var(--t3)" }}>…</div>}
    </div>
  );
}

registerList({ id: "calendar", label: "Calendar", icon: "cal", order: 20, component: CalendarList });
registerTab("meeting", MeetingTab);
registerContext("transcript", TranscriptContext);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeeting(); if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
