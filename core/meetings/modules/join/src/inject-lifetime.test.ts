/**
 * A page.evaluate watcher dies with its document; an addInitScript watcher does not.
 *
 * The bot installs its capture pieces two different ways, and the difference is invisible until a
 * client navigates: the browser bundle and the WebRTC audio hook go in via `context.addInitScript`
 * (re-injected into every document), while the platform speaker watchers went in via a single
 * `page.evaluate`. A watcher is a `setInterval` — so a navigation after join leaves the audio path
 * alive and the NAME path silently dead, which is exactly the shape observed on a live Zoom bot:
 * a correct transcript attributed entirely to `seg_N`, with `bridge-crossed=0` (#852).
 *
 * This pins the mechanism with a DOUBLE rather than a meeting: two intervals, one installed each
 * way, a navigation, and a count of which still ticks. No platform, no account, no flake.
 *
 *   tsx src/inject-lifetime.test.ts
 */
import { chromium } from 'playwright';

let checks = 0;
const ok = (cond: boolean, msg: string): void => {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
};

async function main(): Promise<void> {
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  let evalTicks = 0, initTicks = 0;
  await ctx.exposeFunction('__evalTick', () => { evalTicks++; });
  await ctx.exposeFunction('__initTick', () => { initTicks++; });
  await ctx.addInitScript(`setInterval(() => window.__initTick && window.__initTick(), 50);`);

  const page = await ctx.newPage();
  await page.goto('data:text/html,<h1>one</h1>');
  await page.evaluate(() => setInterval(() => (globalThis as any).__evalTick?.(), 50));
  await page.waitForTimeout(500);
  ok(evalTicks > 0 && initTicks > 0, 'both watchers tick in the document they were installed in');

  await page.goto('data:text/html,<h1>two</h1>');
  const atNav = { evalTicks, initTicks };
  await page.waitForTimeout(700);

  ok(evalTicks - atNav.evalTicks === 0,
    'a page.evaluate watcher stops at a navigation — it died with its document');
  ok(initTicks - atNav.initTicks > 0,
    'an addInitScript watcher keeps ticking — it is re-injected into the new document');

  await browser.close();
  console.log(`\n✅ inject-lifetime: ${checks} checks — capture pieces installed by page.evaluate need re-arming; addInitScript ones do not.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
