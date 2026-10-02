/**
 * browser — the one true persistent-context launch.
 *
 * A *persistent* context (vs a fresh newContext) is what makes authentication work:
 * cookies / localStorage / Login Data written into `dataDir` survive across launches.
 * Carved from the two byte-identical call sites in vexa-bot (browser-session.ts and
 * the index.ts authenticated branch) — same options, single source of truth.
 */
import { chromium } from 'playwright-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import type { BrowserContext, Page } from 'playwright';
import { BROWSER_DATA_DIR } from './session-store';

// Anti-detection: register the stealth evasions ONCE at module load — playwright-extra's `chromium`
// then applies them to every launchPersistentContext below. The two launch flags we already set
// (--disable-blink-features=AutomationControlled + stripping --enable-automation) only mask
// navigator.webdriver; the stealth plugin patches the rest of the fingerprint surface Google Meet's
// anti-abuse reads to bucket a bot into "With potential risks" — navigator.plugins/languages,
// WebGL vendor/renderer, chrome.runtime, permissions, iframe.contentWindow, and more.
chromium.use(StealthPlugin());

export interface LaunchPersistentOptions {
  /** Chromium profile dir — the durable session lives here. Defaults to BROWSER_DATA_DIR. */
  dataDir?: string;
  /** Launch flags — getBrowserSessionArgs() (VNC) or getAuthenticatedBrowserArgs() (bot). */
  args: string[];
  /** Headed by default (Xvfb under VNC); pass true only for headless contexts. */
  headless?: boolean;
  /** Pinned UI locale (#856) — sets navigator.language / Accept-Language on the
   *  context. Defaults to BOT_UI_LOCALE (env), else en-US. Keeps the page-level
   *  locale byte-identical to the --lang launch flag the caller passes in args. */
  locale?: string;
}

export async function launchPersistentBrowser(
  opts: LaunchPersistentOptions,
): Promise<{ context: BrowserContext; page: Page }> {
  const dataDir = opts.dataDir ?? BROWSER_DATA_DIR;
  const locale = opts.locale ?? ((process.env.BOT_UI_LOCALE || '').trim() || 'en-US');
  const context = await chromium.launchPersistentContext(dataDir, {
    headless: opts.headless ?? false,
    ignoreDefaultArgs: ['--enable-automation'],
    args: opts.args,
    viewport: null,
    locale,
  });
  // Anti-detection the stealth plugin misses under playwright-extra. Measured live (with stealth
  // active) that the bot's browser still leaks the tells below; patch them on every frame/navigation.
  await context.addInitScript(() => {
    const g: any = globalThis;
    // WebGL: the container has NO GPU, so ANGLE falls back to SwiftShader — a strong "server/VM/bot"
    // signal Google Meet's anti-abuse reads (and the reason toggling hardware-acceleration can't help:
    // there is no device to accelerate onto). Report a plausible real Intel/Mesa GPU consistent with
    // the Linux UA for the UNMASKED vendor (37445) / renderer (37446) parameters. Covers WebGL1+2.
    const spoof: Record<number, string> = {
      37445: 'Google Inc. (Intel)',
      37446: 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)',
    };
    const patchGL = (proto: any): void => {
      if (!proto || !proto.getParameter) return;
      const orig = proto.getParameter;
      const wrapped = function (this: any, param: number): any {
        return param in spoof ? spoof[param] : orig.call(this, param);
      };
      try { (wrapped as any).toString = orig.toString.bind(orig); } catch { /* keep going */ }
      proto.getParameter = wrapped;
    };
    patchGL(g.WebGLRenderingContext && g.WebGLRenderingContext.prototype);
    patchGL(g.WebGL2RenderingContext && g.WebGL2RenderingContext.prototype);
    // navigator.deviceMemory: real Chrome exposes it; absent on the bot.
    try {
      if (g.navigator && g.navigator.deviceMemory === undefined) {
        Object.defineProperty(g.navigator, 'deviceMemory', { get: () => 8, configurable: true });
      }
    } catch { /* best-effort */ }
    // chrome.runtime: window.chrome is present (stealth) but runtime is missing — a headless tell.
    try {
      if (g.chrome && !g.chrome.runtime) {
        g.chrome.runtime = { id: undefined, connect: () => {}, sendMessage: () => {} };
      }
    } catch { /* best-effort */ }
  });

  const pages = context.pages();
  const page = pages.length > 0 ? pages[0] : await context.newPage();
  return { context: context as BrowserContext, page: page as Page };
}
