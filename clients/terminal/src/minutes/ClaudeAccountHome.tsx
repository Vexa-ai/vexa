"use client";
/** THE «VEXA FOR CLAUDE» HOME — in place of the agent chat, for an account identity names
 *  `account_mode: claude` (founder ruling 2026-10-24: *"у нас они пользуются только звонками и
 *  воркспейсами"*). Vexa runs no agent for it: the person's own Claude listens to their calls and
 *  writes, through Vexa's MCP. So there is no composer and no Home cards here, only the two things
 *  this account has: its calls and its workspaces. */
import "./minutes.css";

export function ClaudeAccountHome({ onCalls, onPages }: { onCalls: () => void; onPages?: () => void }) {
  return (
    <section data-claude-home aria-label="Vexa for Claude" className="vx-admin-home"
      style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 560, margin: "48px auto", padding: "0 16px" }}>
      <h2 style={{ margin: 0 }}>Vexa for Claude</h2>
      <p style={{ margin: 0 }}>
        Your Claude listens to your calls and writes your notes, with Vexa connected to it. Here are
        your calls and your workspaces.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 8 }}>
        <button type="button" data-claude-home-area="calls" className="vx-admin-card" onClick={onCalls}
          style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, textAlign: "left" }}>
          <span className="vx-admin-card-label">Calls</span>
          <span className="vx-admin-card-says">Transcripts and recordings of every call your bot joined</span>
        </button>
        {onPages && (
          <button type="button" data-claude-home-area="workspaces" className="vx-admin-card" onClick={onPages}
            style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, textAlign: "left" }}>
            <span className="vx-admin-card-label">Workspaces</span>
            <span className="vx-admin-card-says">The pages you and your Claude keep, and who you share them with</span>
          </button>
        )}
      </div>
    </section>
  );
}
