"use client";
/** agent-window — the shared agent chat engine. One vertically-stacked window (NO horizontal split):
 *  optional entity strip on top · the conversation (a turn timeline that makes the agent's operations
 *  visible — read/search/edit/git/web steps with live status, not just final text) · the composer ·
 *  proposed actions directly under the input. The right-rail chat and the `meeting` copilot render
 *  through this, so they look and behave like one product. */
import { type CSSProperties, type ReactNode, type RefObject, useEffect, useState } from "react";
import { Icon } from "../ui-kit";
import { Markdown } from "../ui-kit/Markdown";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";

// ── the turn model ────────────────────────────────────────────────────────────────
export type OpStatus = "running" | "done" | "error";
export interface Op { icon: string; label: string; status: OpStatus; file?: string; wrote?: boolean }   // icon ∈ ui-kit; file = workspace doc the op touched
/** The live phase of an in-flight agent turn (see chatStream `ChatPhase`), plus when it began so the UI
 *  can tick an elapsed-seconds counter. Rendered as a verbose status line so the pane never looks frozen. */
export type TurnPhase = "connecting" | "working" | "reconnecting" | "stalled";
export interface TurnStatus { phase: TurnPhase; since: number }
/** THE TURN RAN OUT OF BUDGET (Vexa-ai/vexa#1622) — the harness's own words for how far it got, and
 *  the act it offers. `line` is `done.reason` ("stopped at the tool-call budget after 40 of 40
 *  steps"); `act` is present only when there is something to continue.
 *
 *  A FIELD, not prose appended to `t.text`, which is what F89 did. Text cannot carry a button, and a
 *  turn that stops silently is exactly the failure this replaces — the founder re-typed the same
 *  instruction into three dead turns because the chat showed a finished one each time. */
export interface TurnStopped { line: string; act?: { label: string; instruction: string } }
export type Turn =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "agent"; text: string; ops: Op[]; commit?: string; rejected?: string; status?: TurnStatus | null;
      /** the SERVER's step count for this turn (Vexa-ai/vexa#1622). Absent on a deployment one
       *  release behind, where the op line falls back to counting what this browser saw. */
      steps?: number; stopped?: TurnStopped }
  | { id: string; role: "insight"; t?: string; text: string };

const PHASE_LABEL: Record<TurnPhase, string> = {
  connecting: "Starting agent",
  working: "Working",
  reconnecting: "Reconnecting",
  stalled: "Connection stalled — retrying",
};

/** A live "what's happening" line for the in-flight turn — spinner + phase + elapsed seconds, self-ticking
 *  so a long think / tool run / reconnect reads as ALIVE, not stale. Reconnect/stall use an alert color. */
function StatusLine({ status }: { status: TurnStatus }) {
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const secs = Math.max(0, Math.floor((Date.now() - status.since) / 1000));
  const alert = status.phase === "reconnecting" || status.phase === "stalled";
  // F66 (5): a turn can genuinely think for a long time. Past 30s of one phase the line says so in
  // words — a spinner alone reads as hung, and "still working" is the difference between a product
  // that is slow and a product that is broken.
  const quiet = !alert && secs >= 30;
  const color = alert ? "var(--accent)" : "var(--t3)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, fontSize: 12, color, fontFamily: "var(--mono)" }}>
      <span className="vx-op-spin" style={{ width: 11, height: 11, borderRadius: "50%", border: "1.5px solid var(--line2)", borderTopColor: color, flex: "none" }} />
      <span data-turn-status>{quiet ? "still working" : PHASE_LABEL[status.phase]}{secs >= 2 ? ` · ${secs}s` : ""}{alert ? "" : "…"}</span>
    </div>
  );
}

export const opIcon: Record<string, string> = { read: "file", search: "search", edit: "edit", write: "file", git: "git", web: "web", tool: "zap" };

/** render [[wikilinks]] in agent/insight prose as accented spans (click wiring lives in the entity rail) */
function linkify(text: string): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((p, i) => (p.startsWith("[[") ? <span key={i} style={{ color: "var(--blue)" }}>{p}</span> : <span key={i}>{p}</span>));
}

// ── one operation step (the "what's in works" line) ──────────────────────────────
function OpRow({ op }: { op: Op }) {
  const running = op.status === "running";
  const color = op.status === "error" ? "var(--danger)" : op.status === "done" ? "var(--green)" : "var(--accent)";
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

// ── files the turn created/edited — ACTIONABLE chips: click opens the doc in the pages panel ──
function FileChip({ path }: { path: string }) {
  const name = path.split("/").filter(Boolean).pop() ?? path;
  return (
    <button
      onClick={() => window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path } }))}
      title={path}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: "var(--mono)", color: "var(--blue)", background: "var(--bluebg)", border: "none", borderRadius: 6, padding: "3px 8px", cursor: "pointer" }}>
      <Icon name="edit" size={11} />{name}
    </button>
  );
}

// ── the act a stopped turn offers — one control, in the bubble it belongs to (Vexa-ai/vexa#1622) ──
function StoppedLine({ stopped, onContinue }: { stopped: TurnStopped; onContinue?: () => void }) {
  return (
    <div style={{ marginTop: 9, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      <span style={{ fontSize: 11, color: "var(--t2)", fontFamily: "var(--mono)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 6, padding: "3px 8px" }}>
        <Icon name="zap" size={12} style={{ color: "var(--accent)" }} />{stopped.line}
      </span>
      {stopped.act && onContinue && (
        <button
          data-continue-act
          onClick={onContinue}
          title={stopped.act.instruction}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: "var(--mono)", color: "var(--blue)", background: "var(--bluebg)", border: "none", borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
          {stopped.act.label}
        </button>
      )}
    </div>
  );
}

// ── the conversation: a timeline of user bubbles · agent turns (ops + text) · insights ──
export function Conversation({ turns, busy, empty, onContinue }: {
  turns: Turn[]; busy?: boolean; empty?: ReactNode;
  /** WHERE THE CONTINUE PRESS GOES (Vexa-ai/vexa#1622). Passed in rather than dispatched from here:
   *  the shell owns the act the stopped turn was running, and a same-target act needs that intent —
   *  this component knows a turn, not a target. Absent = no control is drawn, so a surface that
   *  cannot resubmit never shows a button that would do nothing. */
  onContinue?: (turn: Extract<Turn, { role: "agent" }>) => void;
}) {
  const bubble: CSSProperties = { maxWidth: "82%", margin: "0 0 0 auto", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 12, borderTopRightRadius: 4, padding: "8px 12px", fontSize: 13, color: "var(--t1)", lineHeight: 1.5, whiteSpace: "pre-wrap" };
  if (turns.length === 0 && empty) return <>{empty}</>;
  return (
    <>
      {turns.map((t, i) => {
        if (t.role === "user") {
          const queued = t.id.startsWith("q-");   // typed mid-turn; fires when the current turn ends
          return <div key={t.id} style={{ marginBottom: 16 }}>
            <div style={{ ...bubble, opacity: queued ? 0.55 : 1 }}>{t.text}</div>
            {queued && <div style={{ textAlign: "right", fontSize: 10, color: "var(--t3)", fontFamily: "var(--mono)", marginTop: 3 }}>queued</div>}
          </div>;
        }
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
              // ONE line, updated in place: the CURRENT (last) op + a step count — a long tool run
              // must not grow the transcript vertically (founder ruling 2026-08-22).
              <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 10px 5px" }}>
                <OpRow op={t.ops[t.ops.length - 1]} />
                {/* F66: the count ticks from the FIRST step. It used to appear only at two, so the
                    one number that proves a long turn is moving was hidden exactly when the reader
                    starts looking for it.
                    …and the SERVER's count wins once it arrives (Vexa-ai/vexa#1622): this browser
                    counts the `tool-call` events IT saw, which is one event short of the truth on
                    any turn it attached to mid-flight, and the settled number is the one the budget
                    was measured against. */}
                {(() => {
                  const n = typeof t.steps === "number" ? t.steps : t.ops.length;
                  return <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", flex: "none" }}>
                    · {n} step{n === 1 ? "" : "s"}
                  </span>;
                })()}
              </div>
            )}
            {t.text && <div style={{ fontSize: 13.5, color: "var(--t1)", lineHeight: 1.6, maxWidth: 680 }}>
              {/* Mintlify-grade rendering in the OUTPUT too: finished turns compile as MDX (Note/Card/
                  Steps/Tabs + wikilinks, safe plain-markdown fallback); the still-streaming turn uses the
                  light parser and upgrades on completion. */}
              {busy && last ? <Markdown>{t.text}</Markdown> : <MdxDoc>{t.text}</MdxDoc>}
            </div>}
            {(() => {
              // every file the turn WROTE, deduped — the output's actionable surface
              const files = [...new Set(t.ops.filter((o) => o.wrote && o.file).map((o) => o.file as string))];
              return files.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 9 }}>
                  {files.map((f) => <FileChip key={f} path={f} />)}
                </div>
              );
            })()}
            {busy && last && (t.status
              ? <StatusLine status={t.status} />
              : (!t.text && <div style={{ fontSize: 13.5, color: "var(--t3)" }}>…</div>))}
            {t.commit && (
              <div style={{ marginTop: 9, fontSize: 11, color: "var(--green)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--greenbg)", borderRadius: 6, padding: "3px 8px", fontFamily: "var(--mono)" }}>
                <Icon name="git" size={12} />committed · {t.commit.slice(0, 7)}
              </div>
            )}
            {t.rejected && (
              <div style={{ marginTop: 9, fontSize: 11, color: "var(--danger)", display: "inline-flex", alignItems: "center", gap: 6, background: "var(--dangerbg)", borderRadius: 6, padding: "3px 8px" }}>
                <Icon name="x" size={12} />{t.rejected}
              </div>
            )}
            {t.stopped && <StoppedLine stopped={t.stopped} onContinue={onContinue && (() => onContinue(t))} />}
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
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--rail)" }}>
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
