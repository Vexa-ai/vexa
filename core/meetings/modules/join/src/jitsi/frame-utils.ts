import { Frame, Page } from "playwright";

/**
 * Some Jitsi deployments (JaaS / 8x8.vc — Brave Talk and other white-labeled embeds) render the
 * ENTIRE app — prejoin, lobby, conference — inside a cross-origin <iframe>; the top-level page
 * (e.g. talk.brave.com) has no jitsi DOM of its own. Stock meet.jit.si and most self-hosted
 * deployments render at the top level instead. Every selector/API check in this module must
 * therefore try the top frame first (the common case), then fall back to child frames, rather
 * than assuming one or the other.
 */
export function allJitsiFrames(page: Page): (Page | Frame)[] {
  return [page, ...page.frames().filter((f) => f !== page.mainFrame())];
}

/** True if `selector` is visible in the top frame or any child frame. */
export async function isVisibleInAnyFrame(page: Page, selector: string): Promise<boolean> {
  for (const target of allJitsiFrames(page)) {
    const visible = await (target as Page).locator(selector).first()
      .isVisible({ timeout: 200 }).catch(() => false);
    if (visible) return true;
  }
  return false;
}

/** Poll the top frame and every child frame until `selector` is visible somewhere, or timeout.
 *  Returns the frame it was found in (so the caller can scope subsequent interactions to it) or
 *  null. Used to locate the prejoin name input / join button wherever the app actually mounted. */
export async function findFrameWithVisibleSelector(
  page: Page,
  selector: string,
  timeoutMs: number,
): Promise<Page | Frame | null> {
  const deadline = Date.now() + timeoutMs;
  do {
    for (const target of allJitsiFrames(page)) {
      const visible = await (target as Page).locator(selector).first()
        .isVisible({ timeout: 200 }).catch(() => false);
      if (visible) return target;
    }
    await page.waitForTimeout(300);
  } while (Date.now() < deadline);
  return null;
}

/**
 * Post-admission: resolve which frame actually hosts the conference DOM (media elements, hangup
 * button, etc.) — the top frame for stock/self-hosted deployments, or the embedding <iframe> for
 * JaaS/8x8.vc-backed deployments (Brave Talk and other white-labeled embeds). Capture/recording
 * setup (finding <audio>/<video> elements to tap) must target this frame, not assume the top one.
 * Falls back to `page` when nothing matches, preserving prior behavior for the un-iframed case.
 */
export async function resolveConferenceFrame(page: Page): Promise<Page | Frame> {
  for (const target of allJitsiFrames(page)) {
    const hasMedia = await (target as Page).evaluate(
      () => document.querySelectorAll('audio, video').length > 0,
    ).catch(() => false);
    if (hasMedia) return target;
  }
  return page;
}

/** Run a page.evaluate-style body scan (innerText / querySelector checks) across every frame,
 *  returning the first non-empty result per `isEmpty`. `def` is the fallback when no frame
 *  produces a non-empty result (including every frame erroring — detached/cross-origin edge
 *  cases) — callers pick a conservative default per call site.
 *
 *  `arg` is passed through to Playwright's evaluate(fn, arg) as data — `fn` runs inside the
 *  page/frame's own JS context and can only see what's serialized in through `arg`, never
 *  outer Node-side closures (selectors, text lists, etc. must travel this way). */
export async function scanFrames<T, Arg>(
  page: Page,
  fn: (arg: Arg) => T,
  arg: Arg,
  isEmpty: (v: T) => boolean,
  def: T,
): Promise<T> {
  for (const target of allJitsiFrames(page)) {
    try {
      // Playwright's evaluate<Arg, R> overloads can't verify a generic Arg against its internal
      // Unboxed<Arg> mapped type — the `any` cast is compile-time only; `fn`/`arg` still flow
      // through unchanged at runtime.
      const result: T = await (target as Page).evaluate(fn as any, arg);
      if (!isEmpty(result)) return result;
    } catch {
      // cross-origin navigation mid-check / detached frame — try the next one
    }
  }
  return def;
}
