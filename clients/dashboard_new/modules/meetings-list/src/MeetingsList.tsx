/**
 * MeetingsList.tsx — the presentational meetings-list VIEW.
 *
 * Props in, DOM out. NO store, NO fetch, NO ws — the `meetings` array is INJECTED (shaped per the
 * api.v1 `MeetingResponse` modeled in @vexa/dash-contracts), and clicks are reported through the
 * injected `onOpen(meeting)` callback. This is the CLEAN modular replacement for the vendored
 * dashboard's `app/meetings/page.tsx` + `components/meetings/meeting-list.tsx`, which coupled the
 * list to a Zustand store, a Next router, and a fetch loop. Here every one of those is an injected
 * prop or absent.
 *
 * One row per meeting: platform icon, the human title (data.name || data.title || native id) with the
 * native id beneath, a status dot + label, the duration, and the start time. The whole row is the
 * click target → `onOpen(meeting)`.
 *
 * The DOM contract the L4 spec reads:
 *   [data-testid="meetings-list"]                 → the root container
 *   [data-testid="meeting-row"]                   → one per meeting; carries data-meeting-id + data-status
 *     [data-testid="meeting-platform"]            → the platform label (icon alt / name)
 *     [data-testid="meeting-native-id"]           → the native meeting id text
 *     [data-testid="meeting-status"]              → the status dot + label; data-status = raw status
 *     [data-testid="meeting-duration"]            → the formatted duration
 *   [data-testid="meetings-empty"]                → shown instead of rows when meetings is empty
 */
import * as React from "react";
import type { MeetingResponse } from "@vexa/dash-contracts";

export interface MeetingsListProps {
  /** The meetings to render, shaped per api.v1 MeetingResponse (GET /meetings items). */
  meetings: MeetingResponse[];
  /** Called with the clicked meeting when a row is activated (click or Enter/Space). */
  onOpen?: (meeting: MeetingResponse) => void;
  /** Optional message shown when `meetings` is empty. */
  emptyMessage?: string;
}

/** The status → label + dot-color map. Mirrors the vendored MEETING_STATUS_CONFIG vocabulary. */
const STATUS_CONFIG: Record<string, { label: string; dot: string }> = {
  requested: { label: "Requested", dot: "#60a5fa" },
  joining: { label: "Joining", dot: "#60a5fa" },
  awaiting_admission: { label: "Waiting", dot: "#fbbf24" },
  active: { label: "Active", dot: "#34d399" },
  needs_help: { label: "Needs Help", dot: "#fb923c" },
  needs_human_help: { label: "Needs Help", dot: "#fb923c" },
  stopping: { label: "Stopping", dot: "#94a3b8" },
  completed: { label: "Completed", dot: "#34d399" },
  failed: { label: "Failed", dot: "#f87171" },
};

/** Human-readable platform label, used as the icon's alt text and the platform cell text. */
const PLATFORM_LABEL: Record<string, string> = {
  google_meet: "Google Meet",
  teams: "Microsoft Teams",
  zoom: "Zoom",
  browser_session: "Browser",
};

function platformLabel(platform: string | null | undefined): string {
  if (!platform) return "Unknown";
  return PLATFORM_LABEL[platform] ?? platform;
}

function statusConfig(status: string): { label: string; dot: string } {
  return STATUS_CONFIG[status] ?? { label: status || "Unknown", dot: "#9ca3af" };
}

/** The human title for a meeting: data.name || data.title || the native id. */
function meetingTitle(m: MeetingResponse): string {
  const data = (m.data ?? {}) as Record<string, unknown>;
  const name = typeof data.name === "string" ? data.name : undefined;
  const title = typeof data.title === "string" ? data.title : undefined;
  return name || title || m.native_meeting_id || `Meeting ${m.id}`;
}

/** Format the start→end span as a compact duration. "—" when either endpoint is missing. */
function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const minutes = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60000);
  if (Number.isNaN(minutes) || minutes < 0) return "—";
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

/** A small platform glyph. Presentational only — alt text doubles as the platform label. */
function PlatformIcon({ platform }: { platform: string | null | undefined }) {
  const label = platformLabel(platform);
  const initial = label.charAt(0).toUpperCase();
  return (
    <span
      data-testid="meeting-platform"
      aria-label={label}
      title={label}
      role="img"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 24,
        height: 24,
        borderRadius: 6,
        background: "var(--muted, #1f2937)",
        color: "var(--muted-foreground, #9ca3af)",
        fontSize: 12,
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {initial}
    </span>
  );
}

function StatusCell({ status }: { status: string }) {
  const cfg = statusConfig(status);
  return (
    <span
      data-testid="meeting-status"
      data-status={status}
      style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: cfg.dot,
          flexShrink: 0,
        }}
      />
      <span>{cfg.label}</span>
    </span>
  );
}

function MeetingRow({
  meeting,
  onOpen,
}: {
  meeting: MeetingResponse;
  onOpen?: (m: MeetingResponse) => void;
}) {
  const title = meetingTitle(meeting);
  const nativeId = meeting.native_meeting_id ?? "";
  const showNativeBeneath = title !== nativeId && nativeId.length > 0;

  const activate = React.useCallback(() => {
    onOpen?.(meeting);
  }, [onOpen, meeting]);

  return (
    <div
      data-testid="meeting-row"
      data-meeting-id={String(meeting.id)}
      data-status={meeting.status}
      role="button"
      tabIndex={0}
      onClick={activate}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate();
        }
      }}
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto auto",
        alignItems: "center",
        gap: 16,
        padding: "12px 16px",
        borderBottom: "1px solid var(--border, rgba(255,255,255,0.08))",
        cursor: "pointer",
      }}
    >
      <PlatformIcon platform={meeting.platform} />

      <span style={{ minWidth: 0 }}>
        <span style={{ fontWeight: 500, display: "block" }}>{title}</span>
        {showNativeBeneath && (
          <span
            data-testid="meeting-native-id"
            style={{
              display: "block",
              fontFamily: "monospace",
              fontSize: 12,
              color: "var(--muted-foreground, #9ca3af)",
              marginTop: 2,
            }}
          >
            {nativeId}
          </span>
        )}
        {!showNativeBeneath && (
          <span data-testid="meeting-native-id" style={{ display: "none" }}>
            {nativeId}
          </span>
        )}
      </span>

      <StatusCell status={meeting.status} />

      <span data-testid="meeting-duration" style={{ color: "var(--muted-foreground, #9ca3af)" }}>
        {formatDuration(meeting.start_time, meeting.end_time)}
      </span>
    </div>
  );
}

/**
 * The meetings list. Renders one accessible, clickable row per meeting, or an empty-state when there
 * are none. Pure presentation: all data + the open handler are injected.
 */
export function MeetingsList({ meetings, onOpen, emptyMessage = "No meetings yet" }: MeetingsListProps) {
  if (meetings.length === 0) {
    return (
      <div data-testid="meetings-list">
        <div
          data-testid="meetings-empty"
          style={{
            padding: "48px 16px",
            textAlign: "center",
            color: "var(--muted-foreground, #9ca3af)",
          }}
        >
          {emptyMessage}
        </div>
      </div>
    );
  }

  return (
    <div data-testid="meetings-list" role="list">
      {meetings.map((m) => (
        <MeetingRow key={m.id} meeting={m} onOpen={onOpen} />
      ))}
    </div>
  );
}

export default MeetingsList;
