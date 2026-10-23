/** Browser sessions — a short-lived browser a person signs in through, seen live in the page. When
 *  the sign-in is confirmed, meeting-api saves the profile as a bot account. The view opens on one
 *  window event, from either side: the agent's `browser_session_open` tool result (chat.tsx) or the
 *  Bot accounts section of the Connections panel (`startBrowserSession`). */

import { toolResultUiEvent } from "../surfaces/toolUiEvents";

export const BROWSER_SESSION_OPEN = "vexa:browser-session-open";
/** Bot accounts changed (a session saved one, one was deleted) — lists re-read. */
export const BOT_ACCOUNTS_CHANGED = "vexa:bot-accounts-changed";

export type BotPlatform = "google" | "microsoft" | "zoom";
export const PLATFORM_NAMES: Record<BotPlatform, string> = { google: "Google", microsoft: "Microsoft", zoom: "Zoom" };

export type BrowserSessionOpen = { session_id: string; vnc_password: string; platform: string };

const SESSION_ID = /^[a-f0-9]{32}$/;

/** The detail of an open event, or null when it is not one this view can show. */
export function readOpenDetail(detail: unknown): BrowserSessionOpen | null {
  if (!detail || typeof detail !== "object") return null;
  const d = detail as Record<string, unknown>;
  if (typeof d.session_id !== "string" || !SESSION_ID.test(d.session_id)) return null;
  // The password may be absent: an agent-opened view carries the id only and reads the password from
  // meeting-api (GET session, owner only), so it never rides the chat stream.
  const pw = typeof d.vnc_password === "string" ? d.vnc_password : "";
  return { session_id: d.session_id, vnc_password: pw, platform: typeof d.platform === "string" ? d.platform : "" };
}

/** The noVNC client's URL. The password rides the fragment only: it never reaches a server or a log. */
export function viewSrc(sessionId: string, password: string): string {
  const fragment = new URLSearchParams({
    autoconnect: "1", resize: "remote", path: `bot-accounts/sessions/${sessionId}/vnc`, password,
  });
  return `/api/bot-accounts/sessions/${sessionId}/view/vnc.html#${fragment.toString()}`;
}

/** The id in an `open` event's `browser-session:<id>` path (the agent's tool, through the harness). */
export function browserSessionFromOpenPath(path: unknown): BrowserSessionOpen | null {
  if (typeof path !== "string" || !path.startsWith("browser-session:")) return null;
  return readOpenDetail({ session_id: path.slice("browser-session:".length) });
}

export function openBrowserSession(detail: BrowserSessionOpen): void {
  window.dispatchEvent(new CustomEvent(BROWSER_SESSION_OPEN, { detail }));
}

/** A tool result asking the page to show a browser session — `browser_session_open` answering with
 *  `ui_action: "open_browser_session"`. Null for anything else. The chat (a surface, below this shell)
 *  raises the event through `surfaces/toolUiEvents.ts`; this reads it the same way. */
export function browserSessionFromToolResult(tool: string, result: unknown): BrowserSessionOpen | null {
  const ui = toolResultUiEvent(tool, result);
  return ui && ui.name === BROWSER_SESSION_OPEN ? readOpenDetail(ui.detail) : null;
}

/** Open a browser session to sign a bot account in (or sign an existing one in again), then show it. */
export async function startBrowserSession(platform: BotPlatform, owner?: "me" | "company", accountId?: string): Promise<BrowserSessionOpen> {
  const body: Record<string, string> = { platform };
  if (owner) body.owner = owner;
  if (accountId) body.account_id = accountId;
  const r = await fetch("/api/bot-accounts/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Could not start a sign-in browser. Please try again.");
  const detail = readOpenDetail({ ...data, platform: data.platform ?? platform });
  if (!detail) throw new Error("Could not start a sign-in browser. Please try again.");
  openBrowserSession(detail);
  return detail;
}
