import "server-only";

import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";
import {
  type InitialMeetingsPage,
  type RawMeeting,
  MEETINGS_PAGE_SIZE,
  prepareMeetingPage,
} from "@/lib/meeting-list";

interface RawMeetingsResponse {
  meetings?: RawMeeting[];
  has_more?: boolean;
}

export async function loadInitialMeetingsPage(): Promise<InitialMeetingsPage> {
  const apiUrl = process.env.VEXA_API_URL;
  if (!apiUrl) {
    return { state: "fallback", meetings: [], hasMore: false };
  }

  const cookieStore = await cookies();
  const apiKey =
    cookieStore.get(getAuthCookieName())?.value || process.env.VEXA_API_KEY || "";
  const query = new URLSearchParams({
    limit: String(MEETINGS_PAGE_SIZE),
    offset: "0",
    exclude_planned: "true",
  });

  try {
    const response = await fetch(`${apiUrl.replace(/\/+$/, "")}/bots?${query}`, {
      headers: { "X-API-Key": apiKey },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 402) {
      return { state: "subscription_required", meetings: [], hasMore: false };
    }
    if (!response.ok) {
      return { state: "fallback", meetings: [], hasMore: false };
    }

    const data = (await response.json()) as RawMeetingsResponse;
    return {
      state: "ready",
      ...prepareMeetingPage(data.meetings || [], data.has_more ?? false),
    };
  } catch {
    return { state: "fallback", meetings: [], hasMore: false };
  }
}
