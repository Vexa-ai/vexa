"use client";
/** Chat surface — posts a turn to /api/chat (→ agent-api → claude unit) and renders the SSE stream:
 *  streaming bubbles, tool chips, and the git-commit badge. The first real contribution (MVP0). */
import type { CSSProperties } from "react";
import { createStore, useStore } from "../platform";
import { registerSurface } from "../contributions";

interface Msg { role: "user" | "agent"; text: string; tools: string[]; commit?: string; rejected?: boolean; }
const chat = createStore<{ messages: Msg[]; busy: boolean }>({ messages: [], busy: false });

const MVP0_SUBJECT = "u_jane"; // auth wires the real subject later

async function sendChat(text: string) {
  const t = text.trim();
  if (!t || chat.getState().busy) return;
  chat.set((s) => ({ messages: [...s.messages, { role: "user", text: t, tools: [] }, { role: "agent", text: "", tools: [] }], busy: true }));
  const patchAgent = (fn: (m: Msg) => Msg) =>
    chat.set((s) => { const m = [...s.messages]; m[m.length - 1] = fn(m[m.length - 1]); return { ...s, messages: m }; });
  try {
    const resp = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: t, subject: MVP0_SUBJECT }) });
    if (!resp.ok || !resp.body) throw new Error(`chat failed (${resp.status})`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const p of parts) {
        const line = p.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "message-delta") patchAgent((m) => ({ ...m, text: m.text ? `${m.text}\n\n${ev.text}` : ev.text }));
        else if (ev.type === "tool-call") patchAgent((m) => ({ ...m, tools: [...m.tools, ev.tool] }));
        else if (ev.type === "commit") patchAgent((m) => ({ ...m, commit: ev.sha }));
        else if (ev.type === "rejected") patchAgent((m) => ({ ...m, rejected: true, text: m.text || "Write rejected — not conformant with workspace.v1; reverted." }));
        else if (ev.type === "error") patchAgent((m) => ({ ...m, text: `${m.text}\n\nError: ${ev.message}` }));
      }
    }
  } catch (e) {
    patchAgent((m) => ({ ...m, text: `Error: ${e instanceof Error ? e.message : String(e)}` }));
  } finally {
    chat.set((s) => ({ ...s, busy: false }));
  }
}

const bubble = (role: "user" | "agent"): CSSProperties => ({
  background: role === "user" ? "var(--panel2)" : "var(--panel)", border: role === "user" ? "none" : "1px solid var(--line)",
  borderRadius: 12, padding: "10px 13px", maxWidth: "80%", fontSize: 13.5, color: "var(--t1)", whiteSpace: "pre-wrap",
});

function ChatMain() {
  const { messages, busy } = useStore(chat);
  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: "22px 24px" }}>
      {messages.length === 0 && (
        <div style={{ color: "var(--t3)", fontSize: 13, paddingTop: 20 }}>
          Ask the agent to record, research, or restructure knowledge — it writes to your git workspace and commits.
        </div>
      )}
      {messages.map((m, i) => (
        <div key={i} style={{ display: "flex", gap: 11, marginBottom: 18, justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
          {m.role === "agent" && <div style={{ width: 26, height: 26, borderRadius: "50%", background: "var(--accentbg)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flex: "none" }}>V</div>}
          <div style={{ maxWidth: "82%" }}>
            <div style={bubble(m.role)}>{m.text || (busy && i === messages.length - 1 ? "…" : "")}</div>
            {m.tools.length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 7 }}>
                {m.tools.map((t, j) => (
                  <span key={j} style={{ fontSize: 11, color: "var(--t3)", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, padding: "2px 7px" }}>⚙ {t}</span>
                ))}
              </div>
            )}
            {m.commit && (
              <div style={{ fontSize: 11.5, color: "var(--green)", marginTop: 8, fontFamily: "var(--mono)" }}>✓ committed · {m.commit.slice(0, 7)}</div>
            )}
            {m.rejected && (
              <div style={{ fontSize: 11.5, color: "var(--live)", marginTop: 8 }}>✗ reverted — workspace.v1 violation</div>
            )}
          </div>
          {m.role === "user" && <div style={{ width: 26, height: 26, borderRadius: "50%", background: "var(--bluebg)", color: "var(--blue)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flex: "none" }}>DG</div>}
        </div>
      ))}
    </div>
  );
}

registerSurface({
  id: "chat",
  activity: { id: "chat", label: "Chat", icon: "msg", order: 20 },
  views: [{ id: "chat.main", slot: "main", component: ChatMain }],
  composer: { enabled: true, placeholder: "Type / for skills, or ask the agent…", quickChips: ["/research", "/draft", "/routine"] },
  onSubmit: (text) => { void sendChat(text); },
  commands: [
    { id: "routine.create", title: "Save what you did as a routine", skill: "/routine", run: () => {} },
    { id: "research", title: "Research and file to the workspace", skill: "/research", run: () => {} },
    { id: "draft", title: "Draft an email or doc", skill: "/draft", run: () => {} },
  ],
});
