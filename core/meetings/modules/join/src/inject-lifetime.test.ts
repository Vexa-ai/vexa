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
 * This pins the mechanism with a DOUBLE rather than a meeting — and the double is in-process, not a
 * real browser: the node lane installs no Playwright browsers, so `chromium.launch()` is an
 * environment dependency, not a test. The double below encodes Playwright's documented injection
 * contract directly — `addInitScript` scripts re-run against every new document, a `page.evaluate`
 * runs once against the current document and its timers are torn down when that document is replaced
 * — then installs one watcher each way and counts which still ticks across a navigation. No platform,
 * no account, no browser, no flake.
 *
 *   tsx src/inject-lifetime.test.ts
 */

let checks = 0;
const ok = (cond: boolean, msg: string): void => {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
};

/**
 * A minimal model of Playwright's page/context injection lifetime. A document owns the intervals
 * registered against it; replacing the document (a navigation) discards them. `context.addInitScript`
 * scripts are re-run against each new document; `page.evaluate` runs once against whatever document
 * is current when it is called.
 */
type Interval = () => void;

class FakeDocument {
  private intervals: Interval[] = [];
  register(fn: Interval): void { this.intervals.push(fn); }
  tick(): void { for (const fn of this.intervals) fn(); }
}

class FakeContext {
  private initScripts: ((doc: FakeDocument) => void)[] = [];
  addInitScript(fn: (doc: FakeDocument) => void): void { this.initScripts.push(fn); }
  runInitScripts(doc: FakeDocument): void { for (const s of this.initScripts) s(doc); }
  newPage(): FakePage { return new FakePage(this); }
}

class FakePage {
  private doc: FakeDocument;
  constructor(private ctx: FakeContext) {
    this.doc = new FakeDocument();
    this.ctx.runInitScripts(this.doc); // init scripts arm the first document too
  }
  goto(): void {
    // A navigation tears down the old document (and every interval bound to it) and builds a new one.
    this.doc = new FakeDocument();
    this.ctx.runInitScripts(this.doc); // …into which init scripts are re-injected
  }
  evaluate(fn: (doc: FakeDocument) => void): void { fn(this.doc); } // runs once, against the current document
  tick(): void { this.doc.tick(); }
}

function main(): void {
  const ctx = new FakeContext();
  let evalTicks = 0, initTicks = 0;

  // The two install paths the production code uses, modelled exactly as the bot uses them.
  ctx.addInitScript((doc) => doc.register(() => { initTicks++; }));

  const page = ctx.newPage();
  page.evaluate((doc) => doc.register(() => { evalTicks++; }));

  page.tick();
  page.tick();
  ok(evalTicks > 0 && initTicks > 0, 'both watchers tick in the document they were installed in');

  page.goto(); // navigate after join
  const atNav = { evalTicks, initTicks };
  page.tick();
  page.tick();

  ok(evalTicks - atNav.evalTicks === 0,
    'a page.evaluate watcher stops at a navigation — it died with its document');
  ok(initTicks - atNav.initTicks > 0,
    'an addInitScript watcher keeps ticking — it is re-injected into the new document');

  console.log(`\n✅ inject-lifetime: ${checks} checks — capture pieces installed by page.evaluate need re-arming; addInitScript ones do not.`);
}

main();
