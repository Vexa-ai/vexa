"use client";
/** Meetings (mocked backend) — the differentiator flow.
 *  • "calendar" LIST (left): meetings; the live one auto-opens; click any to (re)open its copilot.
 *  • "meeting" TAB (center): the copilot — an in-tab entity rail (participants · mentioned entities ·
 *    proposed actions) + a live insight feed + ask box. Pinned while live.
 *  • "transcript" CONTEXT (right): the real-time transcript.
 *  Reopening a past meeting from the calendar restores the same copilot + transcript. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
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

// ── Meeting COPILOT tab (center) ──────────────────────────────────────────────────
function MeetingTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const m = meetingById(params.meetingId as string);
  const [feed, setFeed] = useState<{ kind: "insight" | "you" | "agent"; text: string }[]>([]);
  const live = m?.status === "live";
  const shown = useReveal(m?.insights.length ?? 0, !!live);
  const insights = (m?.insights ?? []).slice(0, shown);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [shown, feed]);
  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;

  const runAction = (label: string) => setFeed((f) => [...f, { kind: "you", text: label }, { kind: "agent", text: `On it — ${label.toLowerCase()}. (mock) I'll surface the result here and commit anything durable to the workspace.` }]);
  const sec: CSSProperties = { fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", margin: "14px 0 7px" };
  const avatar: CSSProperties = { width: 26, height: 26, borderRadius: "50%", background: "var(--panel2)", color: "var(--t1)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flex: "none" };

  return (
    <div style={{ height: "100%", display: "flex", minHeight: 0, background: "var(--bg)" }}>
      {/* in-tab entity rail */}
      <div style={{ width: 248, flex: "none", borderRight: "1px solid var(--line)", overflowY: "auto", padding: "14px 14px 20px" }}>
        {live && <div style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--live)", marginBottom: 10 }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)" }} />LIVE · {m.platform}</div>}
        <div style={sec}>participants</div>
        {m.participants.map((p) => <div key={p.name} style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 8 }}><div style={avatar}>{p.initials}</div><div><div style={{ fontSize: 12.5, color: "var(--t1)" }}>{p.name}</div>{p.role && <div style={{ fontSize: 11, color: "var(--t3)" }}>{p.role}</div>}</div></div>)}
        <div style={sec}>mentioned</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {m.mentioned.map((e) => <span key={e} onClick={() => layout.openTab({ id: `entity:${e}`, title: e, kind: "chat", params: { subject: "u_jane", session: null }, context: null })} style={{ fontSize: 11.5, color: "var(--blue)", border: "1px solid var(--line2)", borderRadius: 20, padding: "2px 9px", cursor: "pointer" }}>{e}</span>)}
        </div>
        <div style={sec}>proposed actions</div>
        {m.actions.map((a) => (
          <button key={a.id} onClick={() => runAction(a.label)} style={{ display: "block", width: "100%", textAlign: "left", border: "1px solid var(--line2)", borderRadius: 9, background: "var(--panel)", padding: "8px 10px", marginBottom: 7, cursor: "pointer", color: "var(--t1)" }}>
            <div style={{ fontSize: 12.5 }}>{a.label}</div>
            <div style={{ fontSize: 11, color: "var(--t3)", marginTop: 2 }}>{a.detail}</div>
          </button>
        ))}
      </div>
      {/* copilot feed */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "18px 22px" }}>
          <div style={{ fontSize: 12, color: "var(--t3)", marginBottom: 14 }}>Meeting copilot — I follow the conversation and surface what helps. {live ? "Listening…" : "Recording from this meeting."}</div>
          {insights.map((i, idx) => (
            <div key={idx} style={{ display: "flex", gap: 10, marginBottom: 12 }}>
              <Icon name="zap" size={15} /><div><span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--mono)" }}>{i.t}</span><div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.55, marginTop: 2 }}>{i.text.split(/(\[\[[^\]]+\]\])/).map((p, j) => p.startsWith("[[") ? <span key={j} style={{ color: "var(--blue)" }}>{p}</span> : <span key={j}>{p}</span>)}</div></div>
            </div>
          ))}
          {feed.map((f, idx) => <div key={`f${idx}`} style={{ maxWidth: 620, margin: f.kind === "you" ? "0 0 10px auto" : "0 0 10px", background: f.kind === "you" ? "var(--panel2)" : "var(--panel)", border: "1px solid var(--line)", borderRadius: 11, padding: "9px 13px", fontSize: 13.5, color: "var(--t1)", lineHeight: 1.55 }}>{f.text}</div>)}
        </div>
        <div style={{ borderTop: "1px solid var(--line)", padding: "12px 22px 16px", flex: "none" }}>
          <div style={{ maxWidth: 720, margin: "0 auto", border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "10px 12px", display: "flex", alignItems: "center", gap: 10 }}>
            <input placeholder="Ask the copilot about this meeting…" onKeyDown={(e) => { if (e.key === "Enter" && e.currentTarget.value.trim()) { runAction(e.currentTarget.value.trim()); e.currentTarget.value = ""; } }} style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
            <Icon name="send" size={16} />
          </div>
        </div>
      </div>
    </div>
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
