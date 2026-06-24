"use client";
/** Chat — registered as the center tab-kind "chat" (one session per tab). Streams a real agent turn over
 *  /api/chat (SSE) and renders bubbles / tool chips / commit + rejection badges. The composer (with
 *  /-skill autocomplete) lives inside the tab. Each tab instance owns its own message state. */
import { useRef, useState, type CSSProperties } from "react";
import { useService, CommandServiceId } from "../platform";
import { registerTab, registerCommand, type TabProps } from "../contributions";
import { Icon } from "../ui-kit";

interface Msg { role: "user" | "agent"; text: string; tools: string[]; commit?: string; rejected?: string }

function ChatTab({ params }: TabProps) {
  const subject = (params.subject as string) ?? "u_jane";
  const session = (params.session as string | null) ?? null;
  const commands = useService(CommandServiceId);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [value, setValue] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const patchAgent = (fn: (m: Msg) => Msg) => setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? fn(m) : m)));

  const send = async (text: string) => {
    const v = text.trim();
    if (!v || busy) return;
    setMessages((ms) => [...ms, { role: "user", text: v, tools: [] }, { role: "agent", text: "", tools: [] }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: v, subject, session }) });
      const reader = r.body?.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (reader) {
        const { value: chunk, done } = await reader.read();
        if (done) break;
        buf += dec.decode(chunk, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let ev: { type: string; text?: string; tool?: string; sha?: string };
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          if (ev.type === "message-delta") patchAgent((m) => ({ ...m, text: m.text ? `${m.text}\n\n${ev.text}` : ev.text ?? "" }));
          else if (ev.type === "tool-call") patchAgent((m) => ({ ...m, tools: [...m.tools, ev.tool ?? ""] }));
          else if (ev.type === "commit") patchAgent((m) => ({ ...m, commit: ev.sha }));
          else if (ev.type === "rejected") patchAgent((m) => ({ ...m, rejected: "workspace.v1 violation — reverted" }));
        }
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      }
    } finally { setBusy(false); }
  };

  const onSubmit = () => {
    const v = value.trim();
    if (!v) return;
    if (v.startsWith("/")) { const sk = commands.querySkills(v)[0]; if (sk) { void commands.execute(sk.id, v); setValue(""); return; } }
    void send(v);
    setValue("");
  };

  const slash = value.startsWith("/");
  const skills = slash ? commands.querySkills(value) : [];
  const bubble = (m: Msg): CSSProperties => ({ maxWidth: 680, margin: m.role === "user" ? "0 0 0 auto" : "0", background: m.role === "user" ? "var(--panel2)" : "var(--panel)", border: "1px solid var(--line)", borderRadius: 12, padding: "11px 14px", fontSize: 14, color: "var(--t1)", lineHeight: 1.6, whiteSpace: "pre-wrap" });

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "20px 24px" }}>
        {messages.length === 0 && <div style={{ color: "var(--t3)", fontSize: 13, textAlign: "center", marginTop: 40 }}>Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits.</div>}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 14 }}>
            <div style={bubble(m)}>{m.text || (busy && i === messages.length - 1 ? "…" : "")}</div>
            {m.tools.map((t, j) => <span key={j} style={{ display: "inline-flex", gap: 5, alignItems: "center", marginTop: 6, marginRight: 6, fontSize: 11.5, color: "var(--t2)", border: "1px solid var(--line2)", borderRadius: 6, padding: "2px 8px" }}>⚙ {t}</span>)}
            {m.commit && <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--green)" }}>✓ committed · {m.commit.slice(0, 7)}</div>}
            {m.rejected && <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--live)" }}>✗ {m.rejected}</div>}
          </div>
        ))}
      </div>
      <div style={{ borderTop: "1px solid var(--line)", padding: "12px 24px 16px", flex: "none" }}>
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
          {slash && skills.length > 0 && (
            <div style={{ border: "1px solid var(--line2)", borderRadius: 11, background: "var(--panel)", marginBottom: 9, overflow: "hidden" }}>
              {skills.map((c) => <div key={c.id} onMouseDown={() => setValue(c.skill! + " ")} style={{ display: "flex", gap: 10, padding: "9px 12px", cursor: "pointer", fontSize: 13 }}><code style={{ fontFamily: "var(--mono)", color: "var(--accent)", minWidth: 88 }}>{c.skill}</code><span style={{ color: "var(--t3)", fontSize: 12 }}>{c.title}</span></div>)}
            </div>
          )}
          <div style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "11px 12px", display: "flex", alignItems: "center", gap: 11 }}>
            <span style={{ fontFamily: "var(--mono)", color: "var(--t3)", fontSize: 13 }}>/</span>
            <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onSubmit(); }} placeholder="Type / for skills, or ask the agent…" disabled={busy}
              style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
            <button aria-label="Send" onClick={onSubmit} disabled={busy} style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}><Icon name="send" size={16} /></button>
          </div>
        </div>
      </div>
    </div>
  );
}

registerTab("chat", ChatTab);
registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
registerCommand({ id: "skill.routine", title: "Save what you did as a routine", skill: "/routine", run: () => {} });
