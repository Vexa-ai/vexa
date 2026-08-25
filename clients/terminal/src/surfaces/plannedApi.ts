"use client";
/** plannedApi — the planned-meetings + calendar-sync client (gateway-proxied).
 *
 *  A PLANNED meeting is a normal meetings row born in an intent status (`scheduled`/`idle`), no
 *  bot yet: `POST /api/meetings` creates it, `PATCH/DELETE /api/meetings/{id}` edit it BY ROW ID
 *  (link-less plans have no native id to address). Calendar CONNECTIONS (`/api/user/calendars`)
 *  live in the identity domain — the ICS URL is a secret, write-only, never read back. */

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

/** ── Calendar connections (the PLURAL API, #1150) ────────────────────────────────────────────
 *
 *  An account holds up to ten NAMED ICS connections, each with its own auto-join policy and bot
 *  name. `/api/user/calendars*` proxies to identity (config) and meeting-api (sync) through the
 *  same gateway edge as the singular era.
 *
 *  THE FEED ADDRESS IS A CREDENTIAL. It is write-only: a connection is created or updated WITH an
 *  `ics_url`, and the API never returns one. What comes back is `ics_url_set` (does this connection
 *  have a feed) plus `ics_url_masked` (host + short suffix, for recognition only — deliberately not
 *  the address). Nothing in this client ever renders a stored URL back into an input. */

/** How many active connections an account may hold; the eleventh POST is a 409 (docs/api/calendar). */
export const MAX_CALENDARS = 10;

export interface CalendarConnection {
  id: string;
  name: string;
  ics_url_set: boolean;
  ics_url_masked: string | null;   // recognition hint (host + suffix) — NEVER the credential
  auto_join: boolean;
  bot_name: string;
  enabled: boolean;
}

export interface CalendarCreateBody {
  name: string;
  ics_url: string;
  auto_join?: boolean;
  bot_name?: string;
}

/** Any subset; omitted fields keep their stored value. `ics_url` REPLACES the secret feed. */
export interface CalendarUpdateBody {
  name?: string;
  ics_url?: string;
  auto_join?: boolean;
  bot_name?: string;
  enabled?: boolean;
}

export async function listCalendars(): Promise<CalendarConnection[]> {
  const body = await jsonOrThrow<{ calendars?: CalendarConnection[] }>(
    await fetch("/api/user/calendars", { cache: "no-store" }),
  );
  return body?.calendars ?? [];
}

/** 201 → the masked connection. 409 = already at MAX_CALENDARS; 422 = bad name/bot name/feed URL. */
export async function createCalendar(body: CalendarCreateBody): Promise<CalendarConnection> {
  return jsonOrThrow(await fetch("/api/user/calendars", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

export async function updateCalendar(id: string, body: CalendarUpdateBody): Promise<CalendarConnection> {
  return jsonOrThrow(await fetch(`/api/user/calendars/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

/** 204 → the secret is gone immediately; a secret-free tombstone lets sync unpick this source
 *  from planned meetings. Meetings another calendar also feeds survive. */
export async function deleteCalendar(id: string): Promise<void> {
  return jsonOrThrow(await fetch(`/api/user/calendars/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

/** The last sync attempt's outcome (stamped by the background sweep AND by a Sync-now). */
export interface CalendarSyncStamp {
  calendar_id?: string;
  calendar_name?: string;
  last_sync?: string;
  last_error?: string | null;
  counts?: { created?: number; updated?: number; cancelled?: number };
}

/** The retained stamp for ONE connection, or `{}` when it has never synced. */
export async function getCalendarSyncStatus(id: string): Promise<CalendarSyncStamp> {
  return jsonOrThrow(await fetch(`/api/user/calendars/${encodeURIComponent(id)}/sync`, { cache: "no-store" }));
}

/** Sync ONE connection RIGHT NOW → the fresh stamp. A feed fetch/parse fault is a 200 carrying
 *  `last_error` (not a throw); 404 = unknown/disabled/deleted, 503 = sync unwired here. */
export async function syncCalendar(id: string): Promise<CalendarSyncStamp> {
  return jsonOrThrow(await fetch(`/api/user/calendars/${encodeURIComponent(id)}/sync`, { method: "POST" }));
}
