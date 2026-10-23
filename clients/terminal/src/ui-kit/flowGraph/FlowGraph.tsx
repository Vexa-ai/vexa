"use client";
/** THE FLOW, AS BOXES AND ARROWS — read-only, from the flow definition (see `model.ts`). Left to
 *  right on a wide screen, top to bottom on a phone (`flowGraph.css`). A node shows its last-run
 *  state and duration in muted text (accent only for a step running now — the calm-faults rule keeps
 *  red for destructive outcomes); a click peeks its configuration. No library: boxes and an arrow. */
import { useState } from "react";
import { flowGraph, type FlowDef } from "./model";
import "./flowGraph.css";

const STATUS_TEXT: Record<string, string> = {
  ok: "last run ok", refused: "refused", failed: "failed", running: "running now", never: "not run yet",
};

export function FlowGraph({ flow }: { flow: FlowDef }) {
  const nodes = flowGraph(flow);
  const [peek, setPeek] = useState<string | null>(null);
  const shown = nodes.find((n) => n.id === peek);
  return (
    <div data-flow-graph={flow.name} role="group" aria-label={`Flow ${flow.name}`} className="vx-fg">
      <div className="vx-fg-row">
        {nodes.map((n, i) => (
          <div key={n.id} className="vx-fg-cell">
            {i > 0 && (
              <>
                <svg aria-hidden width={22} height={12} className="vx-fg-arrow">
                  <path d="M0 6 H18 M13 2 L19 6 L13 10" stroke="currentColor" fill="none" strokeWidth="1.2" />
                </svg>
                <svg aria-hidden width={12} height={16} className="vx-fg-arrow-v">
                  <path d="M6 0 V12 M2 8 L6 13 L10 8" stroke="currentColor" fill="none" strokeWidth="1.2" />
                </svg>
              </>
            )}
            <button type="button" data-node={n.id} data-kind={n.kind} aria-expanded={peek === n.id}
              onClick={() => setPeek(peek === n.id ? null : n.id)} className="vx-fg-node">
              <span className="vx-fg-label">{n.label}</span>
              {n.sub && <span className="vx-fg-sub">{n.sub}</span>}
              {n.status && (
                <span className="vx-fg-sub" data-status={n.status}>
                  {STATUS_TEXT[n.status]}{n.duration_s ? ` · ${n.duration_s}s` : ""}
                </span>
              )}
            </button>
          </div>
        ))}
      </div>
      {shown && (
        <div role="region" aria-label={shown.detail.title} data-peek={shown.id} className="vx-fg-peek">
          <div className="vx-fg-peek-title">{shown.detail.title}</div>
          {shown.detail.lines.map((l, i) => <div key={i} className="vx-fg-peek-line">{l}</div>)}
        </div>
      )}
    </div>
  );
}
