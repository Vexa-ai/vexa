"use client";
/** Meetings (mocked backend) — the differentiator flow, rendered through the shared agent-window.
 *  • "calendar" LIST (left): meetings; the live one auto-opens; click any to (re)open its copilot.
 *  • "meeting" TAB (center): ONE stacked agent window — a centered list of the meeting's entities
 *    ("in the room" + "detected"); click one to open its entity card in the right sidebar (the agent
 *    creates it first if missing); Research runs in the chat below. The composer + suggested actions
 *    sit under the conversation. No horizontal split, no insight feed.
 *  • "transcript" CONTEXT (right): the real-time transcript (until you open an entity card). */
import { useEffect, useRef, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { registerList, registerTab, registerContext, registerCommand, type TabProps, type ContextProps } from "../contributions";
import { Icon } from "../ui-kit";
import { EntityList, onResearchRequest } from "./entities";
import { MEETINGS, meetingById, liveMeeting, meetingEntities, entityFor, type MeetingMock, type Entity } from "./mock";

export function meetingTab(m: MeetingMock): TabDescriptor {
  return { id: `meeting:${m.id}`, title: m.title.split(" — ")[0], kind: "meeting", params: { meetingId: m.id }, context: { kind: "transcript", params: { meetingId: m.id } } };
}

/** progressive reveal of the transcript (live = stream; past = all at once) */
function useReveal(n: number, live: boolean, stepMs = 3000): number {
  const [k, setK] = useState(live ? Math.min(3, n) : n);
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

// ── Meeting COPILOT tab (center) — entity list + research chat ─────────────────────
function MeetingTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const m = meetingById(params.meetingId as string);
  const live = m?.status === "live";
  const [feed, setFeed] = useState<Turn[]>([]);
  const [value, setValue] = useState("");
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [feed]);

  // a turn whose operations resolve over ~2s, then the reply lands (mock)
  const stream = (label: string, ops: Op[], reply: string) => {
    const n = idRef.current++;
    const aid = `a-${n}`;
    setFeed((f) => [...f, { id: `u-${n}`, role: "user", text: label }, { id: aid, role: "agent", text: "", ops }]);
    const patch = (fn: (t: Extract<Turn, { role: "agent" }>) => Extract<Turn, { role: "agent" }>) =>
      setFeed((f) => f.map((t) => (t.id === aid && t.role === "agent" ? fn(t) : t)));
    ops.forEach((_, k) => setTimeout(() => patch((t) => ({ ...t, ops: t.ops.map((o, j) => (j <= k ? { ...o, status: "done" } : o)) })), 450 * (k + 1)));
    setTimeout(() => patch((t) => ({ ...t, text: reply, commit: "a1b2c3d4" })), 450 * (ops.length + 1));
  };

  const research = (e: Entity) => stream(`Research ${e.title}`, [
    { icon: opIcon.web, label: `web · "${e.title} — latest"`, status: "running" },
    { icon: opIcon.read, label: `read ${e.path}`, status: "running" },
    { icon: opIcon.edit, label: `update ${e.path}`, status: "running" },
  ], `Researched ${e.title} — pulled the latest and appended findings to [[${e.title}]]; committed to the workspace.`);

  const ask = (text: string) => stream(text, [
    { icon: opIcon.read, label: "read kg/entities/company/acme-corp.md", status: "running" },
    { icon: opIcon.search, label: 'search transcript · "renewal", "SSO"', status: "running" },
    { icon: opIcon.edit, label: "edit drafts/acme-renewal.md", status: "running" },
  ], "Done — committed the change to the workspace; it's ready to send on your approval.");

  // open an entity card in the right sidebar (the card creates it first if missing)
  const openEntity = (e: Entity) => layout.setContext({ kind: "entity", params: { title: e.title, from: m?.id } });

  // the active meeting chat is where Research-from-card lands (stream() closes over stable setters)
  useEffect(() => onResearchRequest((title) => research(entityFor(title))), [m?.id]);

  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;
  const { present, detected } = meetingEntities(m);

  const composer = (
    <div style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "10px 12px", display: "flex", alignItems: "center", gap: 10 }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) { ask(value.trim()); setValue(""); } }}
        placeholder="Ask the copilot, or research an entity…" style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
      <button aria-label="Send" onClick={() => { if (value.trim()) { ask(value.trim()); setValue(""); } }} style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}><Icon name="send" size={16} /></button>
    </div>
  );
  const actions = (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 11, color: "var(--t3)", flex: "none" }}>suggested</span>
      {m.actions.map((a) => (
        <button key={a.id} onClick={() => ask(a.label)} title={a.detail}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 20, background: "var(--panel)", color: "var(--t1)", padding: "5px 12px", fontSize: 12.5, cursor: "pointer" }}>
          <Icon name="zap" size={12} style={{ color: "var(--accent)" }} />{a.label}
        </button>
      ))}
    </div>
  );

  return (
    <AgentWindow scrollRef={scrollRef} composer={composer} actions={actions}>
      <div style={{ maxWidth: 680, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 4 }}>
          {live && <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--live)" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)" }} />LIVE</span>}
          <span style={{ color: "var(--t3)" }}>{m.platform} · {m.participants.length} in the room</span>
        </div>
        <div style={{ fontSize: 13, color: "var(--t3)", marginBottom: 18 }}>People and topics from this meeting. Open one to see its card, or research it — I'll work in the chat below.</div>
        <EntityList present={present} detected={detected} onOpen={openEntity} onResearch={research} />
        {feed.length > 0 && (
          <div style={{ borderTop: "1px solid var(--line)", marginTop: 6, paddingTop: 18 }}>
            <Conversation turns={feed} />
          </div>
        )}
      </div>
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
