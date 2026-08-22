"use client";
/** New project — pick the WORKSPACES its chats will see (the one place a picker is the honest
 *  gesture: composing a set is a checkbox decision). Projects are private; workspaces are the
 *  shared thing, made by the scaffold conversation. */
import { useState } from "react";
import type { Membership } from "../surfaces/workspaceApi";

export function ProjectComposer(p: { memberships: Membership[]; onCancel: () => void; onCreate: (name: string, set: string[]) => void }) {
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set(["personal"]));
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const opt = (id: string, label: string, who: string, fixed = false) => (
    <label key={id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", border: "1px solid var(--line2)", borderRadius: 8, cursor: fixed ? "default" : "pointer", fontSize: 13.5, opacity: fixed ? 0.65 : 1, color: "var(--t1)" }}>
      <input type="checkbox" checked={fixed || picked.has(id)} disabled={fixed} onChange={() => toggle(id)} style={{ accentColor: "var(--accent)" }} />
      {label}<span style={{ marginLeft: "auto", fontSize: 11, color: "var(--t3)" }}>{who}</span>
    </label>
  );
  return (
    <div role="dialog" aria-label="New project" style={{ position: "fixed", inset: 0, background: "rgba(10,12,16,.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 40 }}>
      <div style={{ width: 440, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 14, padding: 22 }}>
        <h1 style={{ fontSize: 17, fontWeight: 600, marginBottom: 4 }}>New project</h1>
        <div style={{ fontSize: 12.5, color: "var(--t3)", marginBottom: 14, lineHeight: 1.5 }}>
          A project is your private <b>bundle of workspaces</b> to chat over. Pick what its chats can see —
          writing always lands in one workspace, named per chat.
        </div>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name" autoFocus
          style={{ width: "100%", background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "9px 12px", color: "var(--t1)", fontSize: 14, fontFamily: "inherit", outline: "none", marginBottom: 14 }} />
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 16 }}>
          {opt("personal", "personal", "you")}
          {p.memberships.map((m) => opt(m.workspace_id, m.workspace_id, m.role))}
          {opt("_global", "_global", "everyone · ro", true)}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button onClick={p.onCancel} style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 13, fontWeight: 500, fontFamily: "inherit" }}>Cancel</button>
          <button onClick={() => p.onCreate(name.trim() || "New project", ["_global", ...picked])}
            style={{ background: "var(--accent)", border: "none", borderRadius: 8, color: "#16181d", fontSize: 13, fontWeight: 600, fontFamily: "inherit", padding: "8px 16px", cursor: "pointer" }}>Create project</button>
        </div>
      </div>
    </div>
  );
}
