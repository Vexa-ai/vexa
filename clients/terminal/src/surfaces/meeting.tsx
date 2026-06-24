"use client";
/** Meetings (mocked backend) — the differentiator flow, rendered through the shared agent-window.
 *  • "meetings" LIST (left): meetings; the live one auto-opens; click any to (re)open its copilot.
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
import { MEETINGS, meetingById, liveMeeting, meetingEntities, type MeetingMock, type Entity } from "./mock";
import { useMeetingLive, type LiveCard } from "./meetingLive";
import { useLiveMeetings, liveMeetingsNow } from "./liveMeetings";

// ── live entities (streamed from the real dispatch) — compact, clickable to research ──────────────
const KIND: Record<string, { icon: string; color: string; bg: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)" },
  topic: { icon: "tag", color: "var(--violet)", bg: "var(--violetbg)" },
  action: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
};

/** classify a tool name into one of the op icons so the operation line reads at a glance */
function toolOp(tool: string): Op {
  const t = tool.toLowerCase();
  const icon = /read|cat|open/.test(t) ? opIcon.read : /search|grep|find/.test(t) ? opIcon.search
    : /edit|write|append/.test(t) ? opIcon.edit : /git|commit/.test(t) ? opIcon.git
    : /web|fetch|http/.test(t) ? opIcon.web : opIcon.tool;
  return { icon, label: tool, status: "done" };
}

function LiveCards({ cards, connected, onResearch }: { cards: LiveCard[]; connected: boolean; onResearch: (c: LiveCard) => void }) {
  // the feed re-surfaces the same entity across beats — dedupe by title (first kind/body wins)
  const seen = new Set<string>();
  const uniq = cards.filter((c) => c.title && !seen.has(c.title.toLowerCase()) && seen.add(c.title.toLowerCase()));
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 2px 10px" }}>
        <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600 }}>Entities</span>
        <span style={{ fontSize: 10.5, color: "var(--t3)", fontFamily: "var(--mono)" }}>{uniq.length}</span>
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10.5, color: connected ? "var(--green)" : "var(--t3)" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: connected ? "var(--green)" : "var(--t3)" }} />{connected ? "listening" : "…"}
        </span>
      </div>
      {uniq.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)", padding: "2px 2px" }}>Listening — people, companies, and topics will appear here to click and research.</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {uniq.map((c) => {
          const k = KIND[c.kind] ?? KIND.topic;
          return (
            <button key={c.title} className="vx-fade-up" title={c.body || `Research ${c.title}`} onClick={() => onResearch(c)}
              style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px 5px 6px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer", maxWidth: 280 }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
              <span style={{ width: 18, height: 18, flex: "none", borderRadius: 5, background: k.bg, color: k.color, display: "flex", alignItems: "center", justifyContent: "center" }}><Icon name={k.icon} size={11} /></span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

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

// ── Meetings LIST (left) ─────────────────────────────────────────────────────────
function MeetingsList() {
  const layout = useService(LayoutServiceId);
  const live = useLiveMeetings();                                  // real active copilots (agent-api)
  const liveIds = new Set(live.map((m) => m.id));
  const all = [...live, ...MEETINGS.filter((m) => !liveIds.has(m.id))];  // real live ones on top
  const autoOpened = useRef(false);
  useEffect(() => {                                                // a real live meeting opens itself, once
    if (!autoOpened.current && live.length > 0) {
      autoOpened.current = true;
      layout.openTab(meetingTab(live[0]));
    }
  }, [live, layout]);
  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>meetings</div>
      {all.map((m) => (
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
  const liveList = useLiveMeetings();
  const m = meetingById(params.meetingId as string) ?? liveList.find((x) => x.id === params.meetingId);
  const live = m?.status === "live";
  const [feed, setFeed] = useState<Turn[]>([]);
  const [value, setValue] = useState("");
  const [composerFocus, setComposerFocus] = useState(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [feed]);

  // a REAL agent turn over /api/chat (the same engine as the chat surface): tool-calls stream as ops,
  // then the reply + commit land. One session per meeting so research accumulates context.
  const [busy, setBusy] = useState(false);
  const patchAgent = (fn: (t: Extract<Turn, { role: "agent" }>) => Extract<Turn, { role: "agent" }>) =>
    setFeed((ts) => ts.map((t, i) => (i === ts.length - 1 && t.role === "agent" ? fn(t) : t)));

  const send = async (label: string, prompt: string) => {
    if (busy) return;
    const n = idRef.current++;
    setFeed((f) => [...f, { id: `u-${n}`, role: "user", text: label }, { id: `a-${n}`, role: "agent", text: "", ops: [] }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, subject: "u_live", session: `meeting-${m?.id ?? "live"}` }) });
      const reader = r.body?.getReader(); const dec = new TextDecoder(); let buf = "";
      while (reader) {
        const { value: chunk, done } = await reader.read(); if (done) break;
        buf += dec.decode(chunk, { stream: true });
        const ls = buf.split("\n"); buf = ls.pop() ?? "";
        for (const line of ls) {
          if (!line.startsWith("data: ")) continue;
          let ev: { type: string; text?: string; tool?: string; sha?: string };
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          if (ev.type === "message-delta") patchAgent((t) => ({ ...t, text: t.text ? `${t.text}\n\n${ev.text}` : ev.text ?? "" }));
          else if (ev.type === "tool-call") patchAgent((t) => ({ ...t, ops: [...t.ops, toolOp(ev.tool ?? "tool")] }));
          else if (ev.type === "commit") patchAgent((t) => ({ ...t, commit: ev.sha }));
          else if (ev.type === "rejected") patchAgent((t) => ({ ...t, rejected: "workspace.v1 violation — reverted" }));
        }
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      }
    } finally { setBusy(false); }
  };

  const research = (title: string, kind?: string) =>
    send(`Research ${title}`, `In the live meeting "${m?.title ?? "this meeting"}", the ${kind ?? "entity"} "${title}" came up. Research it (web + the workspace knowledge graph), append a concise note to its entity file, and commit. Keep it tight.`);
  const ask = (text: string) => send(text, text);

  // open an entity card in the right sidebar (the card creates it first if missing)
  const openEntity = (e: Entity) => layout.setContext({ kind: "entity", params: { title: e.title, from: m?.id } });

  // research-from-entity-card requests land in this meeting's chat
  useEffect(() => onResearchRequest((title) => research(title)), [m?.id]);

  // a live-backed meeting subscribes to the REAL dispatch Stream (transcript + copilot cards)
  const liveData = useMeetingLive(m?.id ?? "", (m?.session_uid as string) ?? "");

  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;
  const isLive = !!m.session_uid;
  const { present, detected } = meetingEntities(m);

  const composer = (
    <div style={{ border: `1px solid ${composerFocus ? "var(--accent)" : "var(--line2)"}`, borderRadius: 11, background: "var(--panel)", padding: "9px 9px 9px 13px", display: "flex", alignItems: "center", gap: 10, transition: "border-color .12s ease" }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} onFocus={() => setComposerFocus(true)} onBlur={() => setComposerFocus(false)}
        onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) { ask(value.trim()); setValue(""); } }}
        placeholder="Ask the copilot, or research an entity…" style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 13.5 }} />
      <button aria-label="Send" onClick={() => { if (value.trim()) { ask(value.trim()); setValue(""); } }} disabled={!value.trim()}
        style={{ background: value.trim() ? "var(--accent)" : "var(--panel2)", color: value.trim() ? "#241008" : "var(--t3)", border: "none", width: 30, height: 30, borderRadius: 7, display: "flex", alignItems: "center", justifyContent: "center", cursor: value.trim() ? "pointer" : "default", transition: "background .12s ease, color .12s ease", flex: "none" }}><Icon name="send" size={15} /></button>
    </div>
  );
  const actions = (
    <div className="vx-hscroll" style={{ display: "flex", alignItems: "center", gap: 6, padding: "1px 0" }}>
      <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600, flex: "none", paddingRight: 2 }}>Suggested</span>
      {m.actions.map((a) => (
        <button key={a.id} onClick={() => ask(a.label)} title={a.detail}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 8, background: "var(--panel)", color: "var(--t2)", padding: "5px 10px", fontSize: 12, cursor: "pointer", whiteSpace: "nowrap", flex: "none" }}
          onMouseEnter={(ev) => { ev.currentTarget.style.color = "var(--t1)"; ev.currentTarget.style.background = "var(--panel2)"; }}
          onMouseLeave={(ev) => { ev.currentTarget.style.color = "var(--t2)"; ev.currentTarget.style.background = "var(--panel)"; }}>
          <Icon name="spark" size={12} style={{ color: "var(--accent)" }} />{a.label}
        </button>
      ))}
    </div>
  );

  return (
    <AgentWindow scrollRef={scrollRef} composer={composer} actions={actions}>
      <div style={{ maxWidth: 560, margin: "0 auto" }}>
        <header style={{ marginBottom: 24 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13 }}>
            {live
              ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--live)", fontWeight: 600, letterSpacing: ".04em", fontSize: 11, textTransform: "uppercase" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", boxShadow: "0 0 0 3px var(--livebg)" }} />Live</span>
              : <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase" }}>Ended</span>}
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--t3)" }} />
            <span style={{ color: "var(--t1)", fontWeight: 550 }}>{m.platform}</span>
            <span style={{ color: "var(--t3)" }}>{m.participants.length} in the room</span>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--t3)", lineHeight: 1.5, margin: "6px 0 0", maxWidth: 460 }}>People and topics surfaced from this meeting. Open one for its card, or research it — I'll work in the chat below.</p>
        </header>
        {!isLive && <EntityList present={present} detected={detected} onOpen={openEntity} onResearch={(e) => research(e.title, e.type)} />}
        {isLive && <LiveCards cards={liveData.cards} connected={liveData.connected} onResearch={(c) => research(c.title, c.kind)} />}
        {feed.length > 0 && (
          <div className="vx-fade-up" style={{ borderTop: "1px solid var(--line)", marginTop: 10, paddingTop: 20 }}>
            <Conversation turns={feed} busy={busy} />
          </div>
        )}
      </div>
    </AgentWindow>
  );
}

// ── Transcript CONTEXT (right) ────────────────────────────────────────────────────
function TranscriptContext({ params }: ContextProps) {
  const liveList = useLiveMeetings();
  const m = meetingById(params.meetingId as string) ?? liveList.find((x) => x.id === params.meetingId);
  const isLive = !!m?.session_uid;
  const liveData = useMeetingLive(m?.id ?? "", (m?.session_uid as string) ?? "");
  const scrollRef = useRef<HTMLDivElement>(null);
  const mockLive = m?.status === "live" && !isLive;
  const shown = useReveal(m?.transcript.length ?? 0, mockLive, 3000);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [liveData.transcript.length, shown]);
  if (!m) return <div style={{ padding: 16, color: "var(--t3)" }}>No transcript.</div>;
  const fmt = (t?: number) => (t == null ? "" : `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`);
  const lines = isLive
    ? liveData.transcript.map((s) => ({ t: fmt(s.t), speaker: s.speaker, text: s.text, pending: s.completed === false, id: s.id }))
    : m.transcript.slice(0, shown).map((l) => ({ t: l.t, speaker: l.speaker, text: l.text, pending: false, id: undefined as string | undefined }));
  const streaming = isLive ? (liveData.connected && !liveData.ended) : (m.status === "live" && shown < m.transcript.length);
  const liveDot = isLive ? (liveData.connected && !liveData.ended) : m.status === "live";
  return (
    <div ref={scrollRef} style={{ padding: "14px 16px", height: "100%", overflowY: "auto" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}>
        {liveDot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--live)" }} />}transcript
      </div>
      {lines.map((l, i) => (
        <div key={l.id ?? i} style={{ marginBottom: 11, opacity: l.pending ? 0.5 : 1, transition: "opacity .18s ease" }}>
          <div style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--t3)", marginBottom: 2 }}><span style={{ fontFamily: "var(--mono)" }}>{l.t}</span><span style={{ color: "var(--t2)", fontWeight: 500 }}>{l.speaker}</span>{l.pending && <span style={{ color: "var(--live)", fontFamily: "var(--mono)", fontSize: 9.5, letterSpacing: ".04em" }}>● live</span>}</div>
          <div style={{ fontSize: 13, color: "var(--t1)", lineHeight: 1.5, fontStyle: l.pending ? "italic" : "normal" }}>{l.text}</div>
        </div>
      ))}
      {isLive && lines.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)" }}>Waiting for the transcript…</div>}
      {streaming && <div style={{ fontSize: 12, color: "var(--t3)" }}>…</div>}
    </div>
  );
}

registerList({ id: "meetings", label: "Meetings", icon: "cal", order: 20, component: MeetingsList });
registerTab("meeting", MeetingTab);
registerContext("transcript", TranscriptContext);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeetingsNow()[0] ?? liveMeeting(); if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
