import type { Page } from "playwright";
import { log } from "../_host";
import { isTelemostAdmitted } from "./admission";
import { telemostRemovalTexts } from "./selectors";

export function startTelemostRemovalMonitor(
  page: Page | null,
  onRemoval?: () => void | Promise<void>,
): () => void {
  if (!page) return () => {};
  let stopped = false;
  let misses = 0;
  const started = Date.now();
  const graceMs = 20_000;

  const trigger = async (reason: string) => {
    if (stopped) return;
    stopped = true;
    log(`[Telemost] Removal detected: ${reason}`);
    await onRemoval?.();
  };

  const poll = async () => {
    if (stopped) return;
    if (page.isClosed()) {
      await trigger("page closed");
      return;
    }
    const removalText = await page.evaluate((phrases: string[]) => {
      const body = (document.body?.innerText || "").toLowerCase();
      return phrases.find((text) => body.includes(text.toLowerCase())) || null;
    }, telemostRemovalTexts).catch(() => null);
    if (removalText) {
      await trigger(`matched "${removalText}"`);
      return;
    }
    if (Date.now() - started >= graceMs) {
      if (await isTelemostAdmitted(page)) misses = 0;
      else misses++;
      if (misses >= 3) {
        await trigger("in-meeting controls absent for three polls");
        return;
      }
    }
    if (!stopped) setTimeout(poll, 3000);
  };

  setTimeout(poll, 3000);
  return () => { stopped = true; };
}
