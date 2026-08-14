import { withBasePath } from "@/lib/base-path";

export interface CalendarConnection {
  id: string;
  name: string;
  ics_url_set: boolean;
  ics_url_masked: string | null;
  auto_join: boolean;
  enabled: boolean;
}

export interface CalendarSyncStamp {
  calendar_id?: string;
  calendar_name?: string;
  last_sync: string;
  last_error: string | null;
  counts?: { created: number; updated: number; cancelled: number };
}

export interface CalendarPreferences {
  bot_name: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(withBasePath(`/api/vexa${path}`), {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    try {
      const parsed = JSON.parse(body) as { detail?: string; error?: string };
      throw new Error(parsed.detail || parsed.error || `Request failed (${response.status})`);
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error(body || `Request failed (${response.status})`);
      throw error;
    }
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const calendarAPI = {
  async list(): Promise<CalendarConnection[]> {
    const result = await request<{ calendars: CalendarConnection[] }>("/user/calendars");
    return result.calendars;
  },
  create(input: { name: string; ics_url: string; auto_join: boolean }) {
    return request<CalendarConnection>("/user/calendars", {
      method: "POST", body: JSON.stringify(input),
    });
  },
  update(id: string, input: Partial<Pick<CalendarConnection, "name" | "auto_join" | "enabled">>) {
    return request<CalendarConnection>(`/user/calendars/${encodeURIComponent(id)}`, {
      method: "PATCH", body: JSON.stringify(input),
    });
  },
  disconnect(id: string) {
    return request<void>(`/user/calendars/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  sync(id: string) {
    return request<CalendarSyncStamp>(`/user/calendars/${encodeURIComponent(id)}/sync`, {
      method: "POST",
    });
  },
  status(id: string) {
    return request<CalendarSyncStamp | Record<string, never>>(
      `/user/calendars/${encodeURIComponent(id)}/sync`,
    );
  },
  preferences() {
    return request<CalendarPreferences>("/user/calendar");
  },
  updatePreferences(input: CalendarPreferences) {
    return request<CalendarPreferences>("/user/calendar", {
      method: "PUT", body: JSON.stringify(input),
    });
  },
};
