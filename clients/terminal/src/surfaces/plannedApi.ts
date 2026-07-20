"use client";
/** plannedApi — the planned-meetings + calendar-sync client (gateway-proxied).
 *
 *  A PLANNED meeting is a normal meetings row born in an intent status (`scheduled`/`idle`), no
 *  bot yet: `POST /api/meetings` creates it, `PATCH/DELETE /api/meetings/{id}` edit it BY ROW ID
 *  (link-less plans have no native id to address). The calendar config (`/api/user/calendar`)
 *  lives in the identity domain — the ICS URL is a secret, so reads come back MASKED. */

import { ApiError } from "./apiClient";

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    // Structured failure (P18): carry status + detail so the presenter maps it to user truth.
    let detail = "";
    try {
      const b = (await r.json()) as { detail?: unknown; error?: unknown };
      const d = b?.detail ?? b?.error;
      detail = typeof d === "string" ? d : d != null ? JSON.stringify(d).slice(0, 200) : "";
    } catch { /* body wasn't JSON — the status alone is the signal */ }
    throw new ApiError(r.status, detail, r.url);
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export interface PlannedMeetingBody {
  title?: string | null;
  scheduled_at?: string | null;   // ISO8601; null clears (PATCH) → status flips to idle
  meeting_url?: string | null;    // parsed server-side → platform/native; null detaches the link
  workspace_id?: string | null;   // the sharing bind; null unbinds
  auto_join?: boolean;            // default true on create — "scheduled" means the bot joins
}

/** A meeting row as the list endpoints return it (the DTO subset planned flows care about). */
export interface PlannedMeetingRow {
  id: number;
  platform: string;
  native_meeting_id: string | null;
  status: string;
  constructed_meeting_url?: string | null;
  data: Record<string, unknown>;
}

export async function createPlannedMeeting(body: PlannedMeetingBody): Promise<PlannedMeetingRow> {
  return jsonOrThrow(await fetch("/api/meetings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

export async function updatePlannedMeeting(id: string | number, body: PlannedMeetingBody): Promise<PlannedMeetingRow> {
  return jsonOrThrow(await fetch(`/api/meetings/${encodeURIComponent(String(id))}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

export async function deletePlannedMeeting(id: string | number): Promise<void> {
  return jsonOrThrow(await fetch(`/api/meetings/${encodeURIComponent(String(id))}`, { method: "DELETE" }));
}

export interface CalendarConfig {
  ics_url_set: boolean;
  ics_url_masked: string | null;
  auto_join: boolean;   // the GLOBAL default stamped onto imported meetings
}

export async function getCalendarConfig(): Promise<CalendarConfig> {
  return jsonOrThrow(await fetch("/api/user/calendar", { cache: "no-store" }));
}

export async function setCalendarConfig(body: { ics_url?: string | null; auto_join?: boolean }): Promise<CalendarConfig> {
  return jsonOrThrow(await fetch("/api/user/calendar", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

/** The last sync attempt's outcome (stamped by the sweep AND by syncCalendarNow). */
export interface CalendarSyncStamp {
  last_sync?: string;
  last_error?: string | null;
  counts?: { created?: number; updated?: number; cancelled?: number };
}

export async function getCalendarSyncStatus(): Promise<CalendarSyncStamp> {
  return jsonOrThrow(await fetch("/api/user/calendar/sync", { cache: "no-store" }));
}

/** Run the user's calendar sync RIGHT NOW → the fresh stamp (or throws: 404 = no feed connected). */
export async function syncCalendarNow(): Promise<CalendarSyncStamp> {
  return jsonOrThrow(await fetch("/api/user/calendar/sync", { method: "POST" }));
}
