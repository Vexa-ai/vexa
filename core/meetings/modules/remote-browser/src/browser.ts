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
  // Playwright DISABLES Chromium's sandbox by default (chromiumSandbox:false) — and it does so by
  // INJECTING --no-sandbox itself, which paints the "unsupported command-line flag" banner over the
  // recording no matter what we remove from `args`. The bot runs NON-ROOT under seccomp=unconfined, so
  // the sandbox works: enable it (Playwright then omits the flag → no banner). The CHROME_NO_SANDBOX=1
  // escape hatch (root hosts / no userns) flips it back off, and getSandboxBrowserArgs re-adds the flag.
  const noSandbox = ['1', 'true', 'yes'].includes((process.env.CHROME_NO_SANDBOX || '').trim().toLowerCase());
  const context = await chromium.launchPersistentContext(dataDir, {
    headless: opts.headless ?? false,
    chromiumSandbox: !noSandbox,
    ignoreDefaultArgs: ['--enable-automation'],
    args: opts.args,
    viewport: null,
    locale,
  });
  const pages = context.pages();
  const page = pages.length > 0 ? pages[0] : await context.newPage();
  return { context: context as BrowserContext, page: page as Page };
}
