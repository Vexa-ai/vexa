/**
 * What the Zoom web client shows between navigation and the guest name field.
 *
 * `app.zoom.us/wc/<id>/join?pwd=…` first renders a "Joining Meeting..." shell, then replaces itself
 * client-side with `…/join?wpk=…`. What lives at the `?wpk=` URL is one of:
 *
 *   - the pre-join form (React `#input-for-name`, classic `#inputname`) — the bot types its name;
 *   - the "Error - Zoom" page — the host has not started a meeting that needs the host;
 *   - a self-refreshing HOLD page on Zoom's marketing chrome (OneTrust banner, title "<topic> - Zoom"):
 *     `<div id="prompt"><h4>The host has another meeting in progress</h4> … <p class="msg-reload">The
 *     page will refresh in 5 seconds, or <a id="refresh">reload the page</a> now.</p></div>`. Zoom
 *     serves it while the host's account is in a different meeting; the page reloads itself every
 *     ~5 s and turns into the pre-join once that meeting ends;
 *   - a sign-in page — the host admits only authenticated users.
 *
 * The join reads the page with `readZoomPreJoinSnapshot` (in the browser) and decides with the pure
 * `classifyZoomPreJoin`, over and over until the page settles, so a page that redirects or reloads
 * itself is read again after every navigation instead of once before it.
 */
import type { Page } from "playwright";
import {
  zoomNameInputSelector,
  zoomSelfRefreshingHoldSelector,
  zoomHoldHeadlineSelector,
  zoomAuthRequiredTexts,
  zoomInvalidMeetingTitle,
} from "./selectors";
import { redactMeetingUrl } from "../shared/url-redaction";

/** A reading of the page, taken in the browser. Plain data, so the classification is testable. */
export interface ZoomPreJoinSnapshot {
  url: string;
  title: string;
  /** A name field (React or classic client) is rendered with a box. */
  nameField: boolean;
  /** The self-refreshing hold page is shown; its headline ("" when it has none). */
  hold: { headline: string } | null;
  /** The visible text, whitespace-collapsed, at most 2,000 characters. */
  text: string;
}

export type ZoomPreJoinState =
  | { kind: "name_field" }
  | { kind: "host_not_started" }
  | { kind: "hold"; headline: string }
  | { kind: "auth_required" }
  /** None of the above yet: a redirect or a render is still in flight. */
  | { kind: "pending" };

export interface ZoomPreJoinSelectors {
  name: string;
  hold: string;
  headline: string;
}

export const ZOOM_PREJOIN_SELECTORS: ZoomPreJoinSelectors = {
  name: zoomNameInputSelector,
  hold: zoomSelfRefreshingHoldSelector,
  headline: zoomHoldHeadlineSelector,
};

/**
 * Read the page. Runs inside the browser (`page.evaluate`) and in tests against a jsdom document, so
 * it is one self-contained function over `document` and its argument.
 */
export function snapshotZoomPreJoin(sel: ZoomPreJoinSelectors): ZoomPreJoinSnapshot {
  const name = document.querySelector(sel.name) as HTMLInputElement | null;
  // Rendered = not a hidden input and no ancestor hides it (the same answer in a browser and in jsdom,
  // which lays nothing out).
  let shown = !!name && name.type !== "hidden";
  for (let el: Element | null = name; shown && el; el = el.parentElement) {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || (el as HTMLElement).hidden) shown = false;
  }
  const holdEl = document.querySelector(sel.hold);
  const headlineEl = holdEl ? document.querySelector(sel.headline) : null;
  const body = document.body as HTMLElement | null;
  const text = ((body && (body.innerText || body.textContent)) || "").replace(/\s+/g, " ").trim();
  return {
    url: location.href,
    title: document.title || "",
    nameField: shown,
    hold: holdEl ? { headline: ((headlineEl && headlineEl.textContent) || "").replace(/\s+/g, " ").trim() } : null,
    text: text.slice(0, 2000),
  };
}

/** Read the page; a navigation in flight (the context was torn down mid-read) reads as `null`. */
export async function readZoomPreJoinSnapshot(page: Page): Promise<ZoomPreJoinSnapshot | null> {
  try {
    return await page.evaluate(snapshotZoomPreJoin, ZOOM_PREJOIN_SELECTORS);
  } catch {
    return null;
  }
}

/** Decide what the page is. The name field wins: everything else is a reason it is not there yet. */
export function classifyZoomPreJoin(s: ZoomPreJoinSnapshot | null): ZoomPreJoinState {
  if (!s) return { kind: "pending" };
  if (s.nameField) return { kind: "name_field" };
  if (s.title.toLowerCase() === zoomInvalidMeetingTitle.toLowerCase()) return { kind: "host_not_started" };
  if (s.hold) return { kind: "hold", headline: s.hold.headline };
  const text = s.text.toLowerCase();
  if (zoomAuthRequiredTexts.some((t) => text.includes(t))) return { kind: "auth_required" };
  return { kind: "pending" };
}

/** The evidence a typed pre-join failure carries: where the page was and what it said. */
export function describeZoomPreJoin(s: ZoomPreJoinSnapshot | null): string {
  if (!s) return "page=unreadable (navigation in flight)";
  let where = redactMeetingUrl(s.url);
  try {
    const u = new URL(s.url);
    where = `${u.origin}${u.pathname}${u.searchParams.has("wpk") ? " (after the ?wpk= redirect)" : ""}`;
  } catch { /* keep the raw string */ }
  return `url=${where} title=${JSON.stringify(s.title)} text=${JSON.stringify(s.text.slice(0, 200))}`;
}
