/**
 * WsEventLog.tsx — the presentational WS frame-log debug view.
 *
 * Props in, DOM out. This component owns NO state, NO fetch, NO WebSocket: it renders an injected list
 * of already-summarized WS log events as a terminal-style stream, NEWEST FIRST. Whoever owns the live
 * socket (e.g. @vexa/dash-ws driving @vexa/dash-meeting-state) is responsible for turning raw `ws.v1`
 * frames into `{ ts, type, summary }` rows and handing them down. Keeping the seam here (a flat,
 * pre-summarized row) is what frees this brick from the vendored ws-event-log.tsx's coupling to
 * status/segmentCount/process.env — it just paints rows.
 *
 * The `type` field is typed against the @vexa/dash-contracts WS vocabulary (`WsFrameType`) but stays an
 * open string: the gateway forwards the raw redis payload verbatim and frames are additive, so a row's
 * type may be a tag this brick doesn't model (transcript.mutable, a future control frame, …). We render
 * whatever string arrives.
 *
 * DOM contract (the L4 spec reads exactly this):
 *   [data-testid="ws-event-log"]          the root container
 *   [data-testid="ws-event-row"]          one per event, in render order (newest first)
 *     ├─ [data-testid="ws-event-type"]    the frame type tag
 *     └─ [data-testid="ws-event-summary"] the human summary
 *   [data-testid="ws-event-ts"]           the timestamp (optional per row)
 *   [data-testid="ws-event-empty"]        shown instead of rows when events is empty
 *   [data-testid="ws-event-count"]        the "N events" footer count
 */
import * as React from "react";
import type { WsLogEvent } from "./types.js";

export interface WsEventLogProps {
  /** The WS frames to render, summarized. Rendered NEWEST FIRST regardless of input order. */
  events: WsLogEvent[];
  /** Optional heading shown in the chrome bar. Defaults to "WebSocket". */
  title?: string;
}

/**
 * A coarse colour family per frame type, mirroring the vendored debug view's palette so the modular
 * version is visually familiar. Returned as a data attribute (data-kind) + an inline colour, so the
 * brick needs no CSS framework (Tailwind etc.) and stays drop-in anywhere.
 */
function kindOf(type: string): string {
  if (type === "subscribe" || type === "subscribed" || type === "unsubscribed") return "control";
  if (type === "pong") return "control";
  if (type === "meeting.status") return "status";
  if (type.startsWith("transcript")) return "transcript";
  if (type === "chat_message") return "chat";
  if (type === "error") return "error";
  return "other";
}

const KIND_COLOR: Record<string, string> = {
  control: "#7dd3fc",
  status: "#c4b5fd",
  transcript: "#6ee7b7",
  chat: "#fbbf24",
  error: "#fca5a5",
  other: "#9ca3af",
};

export function WsEventLog({ events, title = "WebSocket" }: WsEventLogProps): React.JSX.Element {
  // newest first — copy before reversing so we never mutate the caller's array
  const ordered = React.useMemo(() => [...events].reverse(), [events]);

  return (
    <div
      data-testid="ws-event-log"
      style={{
        borderRadius: 12,
        overflow: "hidden",
        background: "#111111",
        border: "1px solid var(--border, #2a2a2a)",
        fontFamily:
          "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace)",
        color: "#e5e7eb",
      }}
    >
      {/* chrome bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 14px",
          background: "#1a1a1a",
        }}
      >
        <div style={{ display: "flex", gap: 6 }}>
          <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#ff5f57" }} />
          <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#febc2e" }} />
          <span style={{ width: 11, height: 11, borderRadius: "50%", background: "#28c840" }} />
        </div>
        <span data-testid="ws-event-title" style={{ fontSize: 11, color: "#6b7280" }}>
          {title}
        </span>
      </div>

      {/* event stream — newest first */}
      <div
        style={{
          padding: 14,
          fontSize: 12,
          lineHeight: 1.8,
          maxHeight: 400,
          overflowY: "auto",
        }}
      >
        {ordered.length === 0 ? (
          <div data-testid="ws-event-empty" style={{ color: "#4b5563" }}>
            # Waiting for frames…
          </div>
        ) : (
          ordered.map((event, i) => {
            const kind = kindOf(event.type);
            return (
              <div
                key={i}
                data-testid="ws-event-row"
                data-kind={kind}
                style={{ display: "flex", gap: 8, alignItems: "baseline" }}
              >
                <span style={{ color: "#6ee7b7" }}>←</span>
                {event.ts ? (
                  <span
                    data-testid="ws-event-ts"
                    style={{ color: "#4b5563", flexShrink: 0 }}
                  >
                    {event.ts}
                  </span>
                ) : null}
                <span
                  data-testid="ws-event-type"
                  style={{ color: KIND_COLOR[kind], flexShrink: 0 }}
                >
                  {event.type}
                </span>
                <span
                  data-testid="ws-event-summary"
                  style={{ color: "#9ca3af", wordBreak: "break-all", minWidth: 0 }}
                >
                  {event.summary}
                </span>
              </div>
            );
          })
        )}
      </div>

      {/* footer */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          padding: "6px 14px",
          background: "#161616",
          borderTop: "1px solid rgba(75,85,99,0.5)",
        }}
      >
        <span data-testid="ws-event-count" style={{ fontSize: 10, color: "#4b5563" }}>
          {ordered.length} events
        </span>
      </div>
    </div>
  );
}
