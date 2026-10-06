import { Page } from "playwright";
import { log } from "../_host";
import type { BrowserContextButtonMatcher } from "../shared/leave-click";
import {
  googleChatInputSelectors,
  googleChatOpenMatchers,
  googleChatSendMatchers,
} from "./selectors";

// ── Google Meet in-meeting chat send ──────────────────────────────────────────────
// One DOM routine, shipped into page.evaluate like leaveBrowserClick: opens the chat
// panel, fills the composer, sends. Everything is guarded + boolean-reporting — this
// runs immediately before a leave, so a Meet UI drift or a chat-disabled meeting must
// resolve `false`, never throw into the hang-up path.
//
// Execution-context contract (same as shared/leave-click): the function is serialized
// into the page, where module scope does not exist — DOM globals and its arguments
// only. Matchers therefore travel as the `selectors` argument from selectors.ts,
// which declares them in browserContextSelectorArrays for the validity gate.

/** The single serializable argument the browser routine takes — Playwright's
 *  `page.evaluate(fn, arg)` carries exactly one value into the page. */
export interface GmeetChatBrowserSendArg {
  text: string;
  open: BrowserContextButtonMatcher[];
  input: string[];
  send: BrowserContextButtonMatcher[];
}

/**
 * The chat-send routine that runs INSIDE the meeting page. Exported for tests and parity
 * with googleLeaveBrowserClick: page.evaluate serializes this exact function, so a
 * no-browser fixture drives what production ships.
 */
export async function gmeetChatBrowserSend(arg: GmeetChatBrowserSendArg): Promise<boolean> {
  const { text } = arg;
  const selectors = arg;
  (globalThis as any).__name = (globalThis as any).__name || ((f: unknown) => f);
  const blog = (m: string) => { try { (window as any).logBot?.(m); } catch { /* best-effort */ } };
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const normalize = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const isVisible = (el: Element) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el as HTMLElement);
    return rect.width > 0 && rect.height > 0
      && cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
  };
  const findComposer = (): HTMLElement | null => {
    for (const selector of selectors.input) {
      let el: Element | undefined;
      try {
        el = Array.from(document.querySelectorAll(selector)).find(isVisible);
      } catch (e: any) {
        blog(`[chat] selector failed in browser context: ${selector} — ${e?.message}`);
        continue;
      }
      if (el) return el as HTMLElement;
    }
    return null;
  };
  const clickFirst = (matchers: BrowserContextButtonMatcher[]): boolean => {
    for (const matcher of matchers) {
      const scope = matcher.css ?? 'button, [role="button"]';
      let candidates: Element[];
      try {
        candidates = Array.from(document.querySelectorAll(scope));
      } catch (e: any) {
        blog(`[chat] selector failed in browser context: ${scope} — ${e?.message}`);
        continue;
      }
      const needle = matcher.text === undefined ? null : normalize(matcher.text);
      const button = candidates.find(
        (el) => (needle === null || normalize(el.textContent || "").includes(needle)) && isVisible(el),
      ) as HTMLElement | undefined;
      if (!button) continue;
      button.click();
      return true;
    }
    return false;
  };

  let composer = findComposer();
  if (!composer) {
    // Panel closed — open it via the toolbar affordance.
    if (!clickFirst(selectors.open)) {
      blog("[chat] no chat affordance matched — chat may be disabled for this meeting");
      return false;
    }
    // Wait for the panel to mount before hunting the composer.
    for (let i = 0; i < 10 && !composer; i++) {
      await sleep(300);
      composer = findComposer();
    }
  }
  if (!composer) {
    blog("[chat] chat panel opened but no composer input appeared");
    return false;
  }

  composer.focus();
  if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
    composer.value = text;
    composer.dispatchEvent(new Event("input", { bubbles: true }));
    composer.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    composer.textContent = text;
    composer.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
  }
  await sleep(150);

  if (clickFirst(selectors.send)) {
    blog("[chat] announcement sent via send button");
    return true;
  }

  // No send button reachable — Meet submits the composer on Enter. Keydown alone is not
  // enough for every surface, so send the full press sequence.
  for (const type of ["keydown", "keypress", "keyup"]) {
    composer.dispatchEvent(new KeyboardEvent(type, {
      key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
    }));
  }
  await sleep(150);
  // A successful send clears the composer; read that as the confirmation.
  const cleared = composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement
    ? composer.value.trim() === ""
    : (composer.textContent || "").trim() === "";
  blog(cleared ? "[chat] announcement sent via Enter" : "[chat] send unconfirmed — composer still holds the text");
  return cleared;
}

/** Post `text` to the in-meeting chat of a seated Google Meet session. Never throws;
 *  returns false when chat is unreachable (disabled meeting, UI drift, closed page). */
export async function sendGoogleMeetChatMessage(page: Page | null, text: string): Promise<boolean> {
  if (!page || page.isClosed()) {
    log("[sendGoogleMeetChatMessage] Page is not available or closed.");
    return false;
  }
  try {
    return Boolean(await page.evaluate(gmeetChatBrowserSend, {
      text,
      open: googleChatOpenMatchers,
      input: googleChatInputSelectors,
      send: googleChatSendMatchers,
    }));
  } catch (error: any) {
    log(`[sendGoogleMeetChatMessage] chat send failed: ${error?.message}`);
    return false;
  }
}
