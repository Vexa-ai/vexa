"use client";
/** Chat — the center tab-kind "chat" (one session per tab), rendered through the shared agent-window so
 *  it's the same engine as the meeting copilot. Streams a real agent turn over /api/chat (SSE) into the
 *  turn timeline, surfacing each tool-call as a visible operation (read/search/edit/git/web) with status,
 *  then the message + commit / rejection badge. Composer (with /-skill autocomplete) sits under it. */
import { useRef, useState } from "react";
import { useService, CommandServiceId } from "../platform";
import { registerTab, registerCommand, type TabProps } from "../contributions";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { Icon } from "../ui-kit";

/** classify a tool name into one of the op icons so the operation line reads at a glance */
function toolOp(tool: string): Op {
  const t = tool.toLowerCase();
  const icon = /read|cat|open/.test(t) ? opIcon.read : /search|grep|find/.test(t) ? opIcon.search
    : /edit|write|append/.test(t) ? opIcon.edit : /git|commit/.test(t) ? opIcon.git
    : /web|fetch|http/.test(t) ? opIcon.web : opIcon.tool;
  return { icon, label: tool, status: "done" };
}

function ChatTab({ params }: TabProps) {
  const subject = (params.subject as string) ?? "u_live";  // one workspace shared with meeting research
  const session = (params.session as string | null) ?? null;
  const commands = useService(CommandServiceId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [value, setValue] = useState("");
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const patchAgent = (fn: (t: Extract<Turn, { role: "agent" }>) => Extract<Turn, { role: "agent" }>) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 && t.role === "agent" ? fn(t) : t)));

  const send = async (text: string) => {
    const v = text.trim();
    if (!v || busy) return;
    const n = idRef.current++;
    setTurns((ts) => [...ts, { id: `u-${n}`, role: "user", text: v }, { id: `a-${n}`, role: "agent", text: "", ops: [] }]);
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
          if (ev.type === "message-delta") patchAgent((t) => ({ ...t, text: (t.text ?? "") + (ev.text ?? "") }));
          else if (ev.type === "tool-call") patchAgent((t) => ({ ...t, ops: [...t.ops, toolOp(ev.tool ?? "tool")] }));
          else if (ev.type === "commit") patchAgent((t) => ({ ...t, commit: ev.sha }));
          else if (ev.type === "rejected") patchAgent((t) => ({ ...t, rejected: "workspace.v1 violation — reverted" }));
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

  const composer = (
    <>
      {slash && skills.length > 0 && (
        <div style={{ border: "1px solid var(--line2)", borderRadius: 11, background: "var(--panel)", overflow: "hidden" }}>
          {skills.map((c) => <div key={c.id} onMouseDown={() => setValue(c.skill! + " ")} style={{ display: "flex", gap: 10, padding: "9px 12px", cursor: "pointer", fontSize: 13 }}><code style={{ fontFamily: "var(--mono)", color: "var(--accent)", minWidth: 88 }}>{c.skill}</code><span style={{ color: "var(--t3)", fontSize: 12 }}>{c.title}</span></div>)}
        </div>
      )}
      <div style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "11px 12px", display: "flex", alignItems: "center", gap: 11 }}>
        <span style={{ fontFamily: "var(--mono)", color: "var(--t3)", fontSize: 13 }}>/</span>
        <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onSubmit(); }} placeholder="Type / for skills, or ask the agent…" disabled={busy}
          style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
        <button aria-label="Send" onClick={onSubmit} disabled={busy} style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}><Icon name="send" size={16} /></button>
      </div>
    </>
  );

  return (
    <AgentWindow scrollRef={scrollRef} composer={composer}>
      <Conversation turns={turns} busy={busy} empty={<div style={{ color: "var(--t3)", fontSize: 13, textAlign: "center", marginTop: 40 }}>Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits.</div>} />
    </AgentWindow>
  );
}

registerTab("chat", ChatTab);
registerCommand({ id: "skill.research", title: "Research and file to the workspace", skill: "/research", run: () => {} });
registerCommand({ id: "skill.draft", title: "Draft an email or doc", skill: "/draft", run: () => {} });
registerCommand({ id: "skill.routine", title: "Save what you did as a routine", skill: "/routine", run: () => {} });
