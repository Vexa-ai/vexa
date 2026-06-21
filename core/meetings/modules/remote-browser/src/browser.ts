/**
 * browser — the one true persistent-context launch.
 *
 * A *persistent* context (vs a fresh newContext) is what makes authentication work:
 * cookies / localStorage / Login Data written into `dataDir` survive across launches.
 * Carved from the two byte-identical call sites in vexa-bot (browser-session.ts and
 * the index.ts authenticated branch) — same options, single source of truth.
 */
import { chromium } from 'playwright-extra';
import type { BrowserContext, Page } from 'playwright';
import { BROWSER_DATA_DIR } from './session-store';

export interface LaunchPersistentOptions {
  /** Chromium profile dir — the durable session lives here. Defaults to BROWSER_DATA_DIR. */
  dataDir?: string;
  /** Launch flags — getBrowserSessionArgs() (VNC) or getAuthenticatedBrowserArgs() (bot). */
  args: string[];
  /** Headed by default (Xvfb under VNC); pass true only for headless contexts. */
  headless?: boolean;
}

export async function launchPersistentBrowser(
  opts: LaunchPersistentOptions,
): Promise<{ context: BrowserContext; page: Page }> {
  const dataDir = opts.dataDir ?? BROWSER_DATA_DIR;
  const context = await chromium.launchPersistentContext(dataDir, {
    headless: opts.headless ?? false,
    ignoreDefaultArgs: ['--enable-automation'],
    args: opts.args,
    viewport: null,
  });
  const pages = context.pages();
  const page = pages.length > 0 ? pages[0] : await context.newPage();
  return { context: context as BrowserContext, page: page as Page };
}
