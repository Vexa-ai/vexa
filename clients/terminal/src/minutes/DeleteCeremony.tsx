/** The deletion CEREMONY — destroying or reseeding a workspace must be possible but never casual
 *  (founder ruling 2026-08-22): type the workspace's name to arm the button. `_system` never gets
 *  one of these — sessions/continuity are not a folder you reset. */
import { useState } from "react";

export function DeleteCeremony(p: {
  name: string; verb: "Delete" | "Reset"; detail: string;
  onCancel: () => void; onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const armed = typed.trim() === p.name;
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60 }}>
      <div style={{ width: 440, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 14, padding: 22 }}>
        <h1 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>{p.verb} <span style={{ fontFamily: "var(--mono)", fontSize: 14 }}>{p.name}</span></h1>
        <div style={{ fontSize: 12.5, color: "var(--t2)", lineHeight: 1.55, marginBottom: 14 }}>{p.detail}</div>
        <div style={{ fontSize: 11.5, color: "var(--t3)", marginBottom: 6 }}>Type <b style={{ fontFamily: "var(--mono)" }}>{p.name}</b> to confirm:</div>
        <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter" && armed) p.onConfirm(); if (e.key === "Escape") p.onCancel(); }}
          style={{ width: "100%", background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, padding: "8px 11px", color: "var(--t1)", fontSize: 13, fontFamily: "var(--mono)", outline: "none", marginBottom: 14 }} />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button onClick={p.onCancel} style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 13, fontWeight: 500, fontFamily: "inherit" }}>Cancel</button>
          <button disabled={!armed} onClick={p.onConfirm}
            style={{ background: armed ? "var(--danger)" : "var(--panel2)", border: "none", borderRadius: 8, color: armed ? "#fff" : "var(--t3)", fontSize: 13, fontWeight: 600, fontFamily: "inherit", padding: "8px 16px", cursor: armed ? "pointer" : "default" }}>{p.verb}</button>
        </div>
      </div>
    </div>
  );
}
