"use client";
/** THE «VEXA FOR CLAUDE» HOME — in place of the agent chat, for an account identity names
 *  `account_mode: claude` (founder ruling 2026-10-24: *"у нас они пользуются только звонками и
 *  воркспейсами"*). Vexa runs no agent for it: the person's own Claude listens to their calls and
 *  writes, through Vexa's MCP. So there is no composer and no Home cards here, only the two things
 *  this account has: its calls and its workspaces. */
import "./minutes.css";

export function ClaudeAccountHome({ onCalls, onPages }: { onCalls: () => void; onPages?: () => void }) {
  return (
    <section data-claude-home aria-label="Vexa for Claude" className="vx-claude-home">
      <h2 className="vx-claude-home-title">Vexa for Claude</h2>
      <p className="vx-claude-home-says">
        Your Claude listens to your calls and writes your notes, with Vexa connected to it. Here are
        your calls and your workspaces.
      </p>
      <div className="vx-claude-home-areas">
        <button type="button" data-claude-home-area="calls" className="vx-admin-card vx-claude-home-area" onClick={onCalls}>
          <span className="vx-admin-card-label">Calls</span>
          <span className="vx-admin-card-says">Transcripts and recordings of every call your bot joined</span>
        </button>
        {onPages && (
          <button type="button" data-claude-home-area="workspaces" className="vx-admin-card vx-claude-home-area" onClick={onPages}>
            <span className="vx-admin-card-label">Workspaces</span>
            <span className="vx-admin-card-says">The pages you and your Claude keep, and who you share them with</span>
          </button>
        )}
      </div>
    </section>
  );
}
