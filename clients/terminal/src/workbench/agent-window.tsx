"use client";
/** agent-window — the shared agent chat engine. One vertically-stacked window (NO horizontal split):
 *  optional entity strip on top · the conversation (a turn timeline that makes the agent's operations
 *  visible — read/search/edit/git/web steps with live status, not just final text) · the composer ·
 *  proposed actions directly under the input. The right-rail chat and the `meeting` copilot render
 *  through this, so they look and behave like one product. */
import { type CSSProperties, type ReactNode, type RefObject } from "react";
import { Icon } from "../ui-kit";
import { Markdown } from "../ui-kit/Markdown";

// ── the turn model ────────────────────────────────────────────────────────────────
export type OpStatus = "running" | "done" | "error";
export interface Op { icon: string; label: string; status: OpStatus }   // icon ∈ ui-kit (file/search/edit/git/web/zap…)
export type Turn =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "agent"; text: string; ops: Op[]; commit?: string; rejected?: string }
  | { id: string; role: "insight"; t?: string; text: string };

export const opIcon: Record<string, string> = { read: "file", search: "search", edit: "edit", write: "file", git: "git", web: "web", tool: "zap" };

/** render [[wikilinks]] in agent/insight prose as accented spans (click wiring lives in the entity rail) */
function linkify(text: string): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((p, i) => (p.startsWith("[[") ? <span key={i} style={{ color: "var(--blue)" }}>{p}</span> : <span key={i}>{p}</span>));
}

// ── one operation step (the "what's in works" line) ──────────────────────────────
function OpRow({ op }: { op: Op }) {
  const running = op.status === "running";
  const color = op.status === "error" ? "var(--live)" : op.status === "done" ? "var(--green)" : "var(--accent)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.5, color: running ? "var(--t1)" : "var(--t2)" }}>
      <span style={{ width: 13, flex: "none", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
        {op.status === "done" ? <Icon name="check" size={13} style={{ color }} />
          : op.status === "error" ? <Icon name="x" size={13} style={{ color }} />
          : <span className="vx-op-spin" style={{ width: 11, height: 11, borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: color, flex: "none" }} />}
      </span>
      <Icon name={op.icon} size={12} style={{ color: "var(--t3)", flex: "none" }} />
      <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{op.label}</span>
    </div>
  );
}

// ── the conversation: a timeline of user bubbles · agent turns (ops + text) · insights ──
export function Conversation({ turns, busy, empty }: { turns: Turn[]; busy?: boolean; empty?: ReactNode }) {
  const bubble: CSSProperties = { maxWidth: "82%", margin: "0 0 0 auto", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 12, borderTopRightRadius: 4, padding: "8px 12px", fontSize: 13, color: "var(--t1)", lineHeight: 1.5, whiteSpace: "pre-wrap" };
  if (turns.length === 0 && empty) return <>{empty}</>;
  return (
    <>
      {turns.map((t, i) => {
        if (t.role === "user") return <div key={t.id} style={{ marginBottom: 16 }}><div style={bubble}>{t.text}</div></div>;
        if (t.role === "insight") return (
          <div key={t.id} style={{ display: "flex", gap: 10, marginBottom: 14 }}>
            <Icon name="spark" size={15} style={{ color: "var(--accent)", marginTop: 1, flex: "none" }} />
            <div>{t.t && <span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--mono)" }}>{t.t}</span>}
              <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.55, marginTop: 2 }}>{linkify(t.text)}</div></div>
          </div>
        );
        const last = i === turns.length - 1;
        return (
          <div key={t.id} style={{ marginBottom: 18 }}>
            {t.ops.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, borderLeft: "1.5px solid var(--line2)", paddingLeft: 12, margin: "0 0 10px 5px" }}>
                {t.ops.map((op, j) => <OpRow key={j} op={op} />)}
              </div>
            )}
            {(t.text || (busy && last)) && <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6, maxWidth: 680 }}>{t.text ? <Markdown>{t.text}</Markdown> : <span style={{ color: "var(--t3)" }}>…</span>}</div>}
            {t.commit && (
              <div style={{ marginTop: 9, fontSize: 11, color: "var(--green)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--greenbg)", borderRadius: 6, padding: "3px 8px", fontFamily: "var(--mono)" }}>
                <Icon name="git" size={12} />committed · {t.commit.slice(0, 7)}
              </div>
            )}
            {t.rejected && (
              <div style={{ marginTop: 9, fontSize: 11, color: "var(--live)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--livebg)", borderRadius: 6, padding: "3px 8px" }}>
                <Icon name="x" size={12} />{t.rejected}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// ── the stacked shell: top strip · scrolling conversation · composer · actions-under-input ──
export function AgentWindow({ top, scrollRef, children, composer, actions }: {
  top?: ReactNode; scrollRef?: RefObject<HTMLDivElement | null>; children: ReactNode; composer: ReactNode; actions?: ReactNode;
}) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
      {top}
      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "18px 22px" }}>{children}</div>
      <div style={{ borderTop: "1px solid var(--line)", padding: "12px 22px 14px", flex: "none" }}>
        <div style={{ maxWidth: 760, margin: "0 auto", display: "flex", flexDirection: "column", gap: 9 }}>
          {composer}
          {actions}
        </div>
      </div>
    </div>
  );
}
