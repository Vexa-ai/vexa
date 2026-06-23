/**
 * TranscriptViewer.tsx — the presentational transcript view.
 *
 * Props in, DOM out. No store, no fetch, no ws — data is INJECTED. This is the clean modular
 * counterpart of the vendored dashboard's `components/transcript/transcript-viewer.tsx`
 * (+ `transcript-segment.tsx`), with the coupling stripped: no API client, no export library, no
 * cookies, no AI panel, no auto-scroll side effects, no shadcn/Tailwind component imports. It renders
 * the same three things the vendored viewer renders for a reader — attributed segments
 * (speaker + text + time), a live indicator, and a search box — and nothing else.
 *
 * Typed entirely by `@vexa/dash-contracts` (`TranscriptSegment`). Styling is inline so the component
 * renders identically in a bare browser fixture (the L4 harness) and on the real stack with no global
 * CSS dependency — exactly the brick's real runtime footprint.
 */
import { useMemo, useState } from "react";
import type { TranscriptSegment } from "../../dash-contracts/src/index.ts";

export interface TranscriptViewerProps {
  /** The segments to render, in feed order. Shaped per dash-contracts `TranscriptSegment`. */
  segments: TranscriptSegment[];
  /** When true, show the pulsing "Live" indicator. Default false. */
  isLive?: boolean;
  /**
   * Relative playback position in seconds. When set, the segment whose [start_time, end_time] window
   * contains it is marked active (a left accent bar). Presentational only — no scrolling.
   */
  playbackTime?: number;
  /** Called with the clicked segment's start_time (seconds) and absolute_start_time, if present. */
  onSegmentClick?: (startTimeSeconds: number, absoluteStartTime?: string) => void;
}

// ── A small, dependency-free palette so each speaker gets a stable color. Mirrors the vendored
//    getSpeakerColor(speaker, speakerList) ordering: nth distinct speaker → nth palette entry. ──────
const SPEAKER_COLORS = [
  "#1d4ed8", // blue-700
  "#047857", // emerald-700
  "#7e22ce", // purple-700
  "#b45309", // amber-700
  "#be123c", // rose-700
  "#0e7490", // cyan-700
  "#4338ca", // indigo-700
  "#0f766e", // teal-700
];

function colorForSpeaker(speaker: string, speakerOrder: string[]): string {
  const i = speakerOrder.indexOf(speaker);
  return SPEAKER_COLORS[(i < 0 ? 0 : i) % SPEAKER_COLORS.length];
}

/** Render text with case-insensitive <mark> highlights for the query, or the plain text if no query. */
function renderHighlighted(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark
        key={i}
        style={{ background: "#fef08a", borderRadius: 2, padding: "0 1px" }}
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

/** mm:ss for a relative seconds value (the vendored formatTimestamp). */
function formatRelative(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** HH:MM:SS in the viewer's local tz for an absolute ISO time (the vendored formatAbsoluteTimestamp). */
function formatAbsolute(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function toSeconds(v: number | string | undefined): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isNaN(n) ? 0 : n;
  }
  return 0;
}

export function TranscriptViewer({
  segments,
  isLive = false,
  playbackTime,
  onSegmentClick,
}: TranscriptViewerProps) {
  const [query, setQuery] = useState("");

  // Distinct speakers in order of first appearance — drives stable per-speaker coloring.
  const speakerOrder = useMemo(() => {
    const order: string[] = [];
    for (const seg of segments) {
      const sp = seg.speaker ?? "";
      if (sp && !order.includes(sp)) order.push(sp);
    }
    return order;
  }, [segments]);

  // Search filter: a segment matches if its text OR speaker contains the query (case-insensitive).
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return segments;
    return segments.filter(
      (seg) =>
        (seg.text ?? "").toLowerCase().includes(q) ||
        (seg.speaker ?? "").toLowerCase().includes(q),
    );
  }, [segments, query]);

  // Active-during-playback index over the FILTERED list (presentational accent only).
  const activeIndex = useMemo(() => {
    if (playbackTime == null) return -1;
    for (let i = filtered.length - 1; i >= 0; i--) {
      const start = toSeconds(filtered[i].start_time);
      const end = toSeconds(filtered[i].end_time);
      if (start <= playbackTime && playbackTime <= end + 1) return i;
    }
    return -1;
  }, [filtered, playbackTime]);

  return (
    <div data-testid="transcript-viewer" style={styles.root}>
      {/* ── Header: title + live indicator + search ─────────────────────────────────────────────── */}
      <div style={styles.header}>
        <div style={styles.headerTop}>
          <span style={styles.title}>Transcript</span>
          {isLive && (
            <span data-testid="live-indicator" style={styles.live}>
              <span style={styles.liveDot} />
              Live
            </span>
          )}
        </div>
        <input
          data-testid="transcript-search"
          type="text"
          placeholder="Search transcript..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={styles.search}
        />
      </div>

      {/* ── Body: attributed segments, or an empty state ────────────────────────────────────────── */}
      <div data-testid="transcript-body" style={styles.body}>
        {filtered.length === 0 ? (
          <div data-testid="transcript-empty" style={styles.empty}>
            {segments.length === 0
              ? isLive
                ? "Waiting for speech to transcribe..."
                : "No transcript available"
              : "No results found"}
          </div>
        ) : (
          filtered.map((seg, i) => {
            const speaker = seg.speaker ?? "";
            const color = colorForSpeaker(speaker, speakerOrder);
            const startSeconds = toSeconds(seg.start_time);
            const time = seg.absolute_start_time
              ? formatAbsolute(seg.absolute_start_time)
              : formatRelative(startSeconds);
            const isActive = i === activeIndex;
            const clickable = !!onSegmentClick;
            return (
              <div
                key={seg.segment_id ?? `${speaker}-${startSeconds}-${i}`}
                data-testid="transcript-segment"
                data-speaker={speaker}
                onClick={
                  clickable
                    ? () =>
                        onSegmentClick!(
                          startSeconds,
                          seg.absolute_start_time ?? undefined,
                        )
                    : undefined
                }
                style={{
                  ...styles.segment,
                  ...(isActive ? styles.segmentActive : null),
                  cursor: clickable ? "pointer" : "default",
                }}
              >
                <div style={styles.segmentMeta}>
                  <span
                    data-testid="segment-speaker"
                    style={{ ...styles.speaker, color }}
                  >
                    {speaker || "Unknown"}
                  </span>
                  <span data-testid="segment-time" style={styles.time}>
                    {time}
                  </span>
                </div>
                <p
                  data-testid="segment-text"
                  style={{
                    ...styles.text,
                    ...(seg.completed === false ? styles.textPending : null),
                  }}
                >
                  {renderHighlighted(seg.text ?? "", query.trim())}
                </p>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── Inline styles: zero global-CSS dependency so the component paints identically in the bare L4
//    fixture and on the real stack. ─────────────────────────────────────────────────────────────────
// Colors read the host's design tokens (shadcn CSS variables) with a FALLBACK to the original literal —
// so the brick themes (light/dark) inside the app, AND still paints identically in a bare L4 fixture
// where the vars are undefined (the fallback wins). Framework-light, themeable.
const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    flexDirection: "column",
    fontFamily: "var(--font-sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif)",
    color: "var(--card-foreground, #0f172a)",
    border: "1px solid var(--border, #e2e8f0)",
    borderRadius: 8,
    background: "var(--card, #ffffff)",
    overflow: "hidden",
  },
  header: {
    padding: "10px 12px",
    borderBottom: "1px solid var(--border, #e2e8f0)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  headerTop: { display: "flex", alignItems: "center", gap: 8 },
  title: { fontWeight: 600, fontSize: 14 },
  live: {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    fontSize: 12,
    fontWeight: 600,
    color: "var(--destructive, #dc2626)",
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: "var(--destructive, #dc2626)",
    display: "inline-block",
  },
  search: {
    width: "100%",
    boxSizing: "border-box",
    padding: "6px 10px",
    fontSize: 13,
    color: "var(--foreground, #0f172a)",
    background: "var(--background, #ffffff)",
    border: "1px solid var(--input, #cbd5e1)",
    borderRadius: 6,
    outline: "none",
  },
  body: {
    flex: 1,
    minHeight: 0,
    overflowY: "auto",
    padding: "4px 0",
  },
  empty: {
    padding: "32px 16px",
    textAlign: "center",
    color: "var(--muted-foreground, #64748b)",
    fontSize: 13,
  },
  segment: {
    padding: "6px 12px",
    transition: "background 120ms",
  },
  segmentActive: {
    background: "var(--accent, #eff6ff)",
    borderLeft: "2px solid var(--primary, #2563eb)",
  },
  segmentMeta: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 2,
  },
  speaker: { fontWeight: 600, fontSize: 13 },
  time: { fontSize: 11, color: "var(--muted-foreground, #64748b)" },
  text: { margin: 0, fontSize: 13, lineHeight: 1.4 },
  textPending: { color: "var(--muted-foreground, #94a3b8)", fontStyle: "italic" },
};
