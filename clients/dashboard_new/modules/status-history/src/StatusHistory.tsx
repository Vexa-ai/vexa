/**
 * StatusHistory.tsx — the presentational status-timeline view.
 *
 * Props in, DOM out. NO store, NO fetch, NO ws — the transitions array is INJECTED by the caller
 * (the dashboard wires it from a meeting's `status_transition` field). The component is typed by
 * @vexa/dash-contracts: each transition's `to`/`from` is a `MeetingStatus` (the sealed status union),
 * carried open as `| string` because the live `meeting.status` frame is additive.
 *
 * It renders the transitions oldest → newest, one row per transition, each row showing the
 * destination status (`to`) + its time (and optional `from`, `reason`/`completion_reason`, `source`).
 * The newest row is marked `data-current` so a caller can style "where we are now".
 *
 * DOM contract (what a test / caller can rely on):
 *   <ol class="status-history" data-count="N">
 *     <li class="status-history__row" data-status="<to>" data-index="i" [data-current]>
 *       <span class="status-history__status">…label…</span>
 *       <time class="status-history__time" dateTime="<iso>">…HH:MM:SS…</time>
 *       [<span class="status-history__from">…from label…</span>]
 *       [<span class="status-history__reason">…reason…</span>]
 *       [<span class="status-history__source">…source…</span>]
 *     </li> …
 *   </ol>
 * Empty / missing transitions → renders nothing (returns null), matching the reference behavior.
 */
import type { CSSProperties } from "react";
import type { MeetingStatus } from "@vexa/dash-contracts";

/**
 * One status transition. Mirrors the backend's `status_transition[]` entry. `to` + `timestamp` are
 * the floor; `from`, `reason`, `completion_reason`, `source` are carried when present. `to`/`from`
 * are the dash-contracts `MeetingStatus` union, kept open (`| string`) because the status stream is
 * additive (a new backend status must still render, never crash the view).
 */
export interface StatusTransition {
  from?: MeetingStatus | string;
  to: MeetingStatus | string;
  timestamp: string;
  reason?: string;
  completion_reason?: string;
  source?: string;
}

export interface StatusHistoryProps {
  transitions?: StatusTransition[];
  className?: string;
}

/** Human label for a status value; unknown values pass through verbatim (additive-safe). */
const STATUS_LABELS: Record<string, string> = {
  requested: "Requested",
  joining: "Joining",
  awaiting_admission: "Waiting",
  active: "Active",
  needs_help: "Needs help",
  needs_human_help: "Needs help",
  stopping: "Stopping",
  completed: "Completed",
  failed: "Failed",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/**
 * Semantic dot color per status — the timeline's only data-encoding color, so it stays a literal
 * (a single token can't carry per-status meaning). completed→green, failed→red, active→blue (primary),
 * everything else→muted. Each keeps a token where it has a natural role (the live/active = primary
 * accent), with the literal as fallback for the bare fixture.
 */
const DOT_COLOR: Record<string, string> = {
  completed: "#22c55e",
  active: "var(--primary, #3b82f6)",
  joining: "var(--primary, #3b82f6)",
  awaiting_admission: "#fbbf24",
  needs_help: "#fb923c",
  needs_human_help: "#fb923c",
  failed: "var(--destructive, #ef4444)",
  stopping: "var(--muted-foreground, #94a3b8)",
};

function dotColor(status: string): string {
  return DOT_COLOR[status] ?? "var(--muted-foreground, #94a3b8)";
}

// ── Inline styles: zero global-CSS dependency so the timeline paints identically in the bare L4
//    fixture and on the real stack. Colors read the host's shadcn tokens with a FALLBACK to the
//    original literal, so the brick themes (light/dark) in the app yet renders unchanged in a fixture
//    where the vars are undefined. ─────────────────────────────────────────────────────────────────
const styles: Record<string, CSSProperties> = {
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    fontFamily: "var(--font-sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif)",
  },
  row: {
    display: "flex",
    alignItems: "baseline",
    gap: 8,
    padding: "4px 0",
    borderLeft: "2px solid var(--border, #e2e8f0)",
    paddingLeft: 12,
    position: "relative",
  },
  dot: {
    position: "absolute",
    left: -5,
    top: 7,
    width: 8,
    height: 8,
    borderRadius: "50%",
    flexShrink: 0,
  },
  status: { fontWeight: 600, fontSize: 13, color: "var(--foreground, #0f172a)" },
  time: { fontSize: 11, color: "var(--muted-foreground, #64748b)", fontVariantNumeric: "tabular-nums" },
  from: { fontSize: 11, color: "var(--muted-foreground, #64748b)" },
  reason: { fontSize: 12, color: "var(--muted-foreground, #64748b)", fontStyle: "italic" },
  source: { fontSize: 11, color: "var(--muted-foreground, #94a3b8)" },
};

/**
 * Format an ISO/UTC timestamp to HH:MM:SS for the row. Backend timestamps may omit the trailing `Z`;
 * we treat a bare `YYYY-MM-DDTHH:MM:SS` as UTC so the clock matches the server (same rule the
 * reference dashboard's parseUTCTimestamp applies). Unparseable input passes through unchanged.
 */
function formatTime(timestamp: string): string {
  const normalized =
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(timestamp)
      ? timestamp + "Z"
      : timestamp;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return timestamp;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

/** Stable epoch-ms for sorting; NaN-safe (unparseable timestamps keep their original order). */
function sortKey(timestamp: string): number {
  const t = new Date(/Z$|[+-]\d{2}:?\d{2}$/.test(timestamp) ? timestamp : timestamp + "Z").getTime();
  return Number.isNaN(t) ? 0 : t;
}

export function StatusHistory({ transitions, className }: StatusHistoryProps) {
  if (!transitions || transitions.length === 0) {
    return null;
  }

  // oldest → newest, the order a timeline reads top-to-bottom. Stable: equal keys keep input order.
  const sorted = transitions
    .map((t, i) => ({ t, i }))
    .sort((a, b) => sortKey(a.t.timestamp) - sortKey(b.t.timestamp) || a.i - b.i)
    .map(({ t }) => t);

  return (
    <ol
      className={["status-history", className].filter(Boolean).join(" ")}
      data-count={sorted.length}
      style={styles.list}
    >
      {sorted.map((transition, index) => {
        const isCurrent = index === sorted.length - 1;
        const reason = transition.reason ?? transition.completion_reason;
        return (
          <li
            key={index}
            className="status-history__row"
            data-status={transition.to}
            data-index={index}
            {...(isCurrent ? { "data-current": "" } : {})}
            style={styles.row}
          >
            <span
              className="status-history__dot"
              aria-hidden="true"
              style={{ ...styles.dot, background: dotColor(transition.to) }}
            />
            <span className="status-history__status" style={styles.status}>
              {statusLabel(transition.to)}
            </span>
            <time className="status-history__time" dateTime={transition.timestamp} style={styles.time}>
              {formatTime(transition.timestamp)}
            </time>
            {transition.from ? (
              <span className="status-history__from" style={styles.from}>
                from {statusLabel(transition.from)}
              </span>
            ) : null}
            {reason ? (
              <span className="status-history__reason" style={styles.reason}>
                {reason}
              </span>
            ) : null}
            {transition.source ? (
              <span className="status-history__source" style={styles.source}>
                {transition.source.replace(/_/g, " ")}
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export default StatusHistory;
