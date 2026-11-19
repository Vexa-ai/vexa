#!/usr/bin/env node
/**
 * style-attr-sweep — the terminal pages' `style-src-attr` proof, in a real browser.
 *
 *   NODE_PATH=<dir>/node_modules node scripts/style-attr-sweep.mjs <origin> \
 *     [--pages signin,shell,…] [--themes dark,light] [--shots <dir>] [--json out.json] [--attr-none]
 *
 * It opens each key page of a running PRODUCTION terminal and records every
 * `securitypolicyviolation` the page raises, with the element and the attribute's first characters,
 * then takes a full-page screenshot per page and theme. Exit 1 when any page raised one, or when a
 * page's policy, as served, is not `style-src-attr 'none'`.
 *
 * `--attr-none` rewrites the document's policy to `style-src-attr 'none'` on the way in, so a build
 * whose header still allows inline attributes can be MEASURED: what would break if it did not. The
 * shipped policy needs no rewrite.
 *
 * The signed-in pages need no backend: the probe answers `/api/*` itself (a signed-in person, an
 * instance with an admin, three meetings — held, live, planned) and opens the Minutes shell with `?mock=1`, the shell's own
 * fabricated meetings (minutes/mockPhases.ts) — prep, live and post. The design catalogue and the
 * MDX demo need `VEXA_TERMINAL_DESIGN_CATALOGUE=1` on the server.
 *
 * `--compare <dirA> <dirB>` compares two screenshot directories pixel by pixel IN THE BROWSER (no
 * image library) and prints, per file, the share of pixels that differ.
 *
 * It needs Playwright (Apache-2.0), which is NOT a dependency of the terminal: run it where
 * Playwright is installed, with `NODE_PATH` pointing at it. `PLAYWRIGHT_CHROMIUM` names a browser
 * binary when the bundled one is not installed.
 */
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const launch = () => chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined });

/** The key pages: a path, whether the session is mocked, and what to do once it settled. */
export const PAGES = {
  signin: { path: "/", session: false },
  shell: { path: "/?mock=1", session: true },
  chat: { path: "/?mock=1", session: true, click: ["text=Fernhill discovery", "text=Fernhill Loyalty Card — discovery"] },
  prep: { path: "/?mock=1", session: true, click: ["text=Acme pricing review", "text=Acme — pricing review"] },
  live: { path: "/?mock=1", session: true, click: ["text=Standup sync", "text=Standup"] },
  settings: { path: "/?mock=1", session: true, click: ["[aria-label*='ettings']", "[title*='ettings']"] },
  meetings: { path: "/meetings/google_meet%2Fabc-defg-hij", session: true },
  canvas: { path: "/w/kg/entities/meeting/mock-post.md", session: true },
  join: { path: "/join", session: false },
  decide: { path: "/decide/probe", session: false },
  profile: { path: "/p/probe", session: false },
  notfound: { path: "/no-such-page", session: false },
  design: { path: "/design", session: false },
  designShell: { path: "/design?view=shell", session: false },
  mdx: { path: "/mdx-demo", session: false },
};

const USER = { id: "probe-user", email: "probe@example.com", name: "Probe Person" };
const at = (mins) => new Date(Date.now() + mins * 60000).toISOString();
/** Three meetings, one per phase: held, live, and planned (the room's Prep). */
const MEETINGS = [
  { id: 9001, platform: "google_meet", native_meeting_id: "abc-defg-hij", status: "completed", start_time: at(-1500), end_time: at(-1452), data: { title: "Fernhill discovery" } },
  { id: 9002, platform: "google_meet", native_meeting_id: "klm-nopq-rst", status: "active", start_time: at(-12), data: { title: "Standup sync" } },
  { id: 9003, platform: "google_meet", native_meeting_id: "uvw-xyza-bcd", status: "requested", created_at: at(-60), data: { title: "Acme pricing review", scheduled_at: at(75) } },
];

/** The probe's stand-in for the API: enough for the gate to let a person in and for the shell to
 *  draw its empty state. Anything it does not know is an empty answer, never a network error. */
function apiAnswer(url, session) {
  const p = new URL(url).pathname;
  if (p === "/api/auth/me") return session ? { status: 200, body: { user: USER, ...USER } } : { status: 401, body: {} };
  if (p === "/api/auth/instance") return { status: 200, body: { admin_exists: true, email_link: true } };
  if (p === "/api/auth/providers") return { status: 200, body: {} };
  if (p === "/api/version") return { status: 200, body: { version: "probe" } };
  if (p === "/api/meetings") return { status: 200, body: { meetings: MEETINGS } };
  return { status: 404, body: { detail: "probe: not served" } };
}

async function openPage(browser, origin, page, theme, attrNone) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme });
  await context.addInitScript(({ theme }) => {
    try { localStorage.setItem("vexa.terminal.theme", theme); } catch { /* storage off */ }
    window.__violations = [];
    const describe = (el) => {
      if (!el || !el.tagName) return String(el);
      const cls = typeof el.className === "string" ? el.className : (el.getAttribute && el.getAttribute("class")) || "";
      return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${cls ? "." + cls.trim().split(/\s+/).slice(0, 3).join(".") : ""}`;
    };
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__violations.push({
        directive: e.effectiveDirective || e.violatedDirective, sample: e.sample, blocked: e.blockedURI,
        source: e.sourceFile, line: e.lineNumber, column: e.columnNumber,
        element: describe(e.target), parent: describe(e.target && e.target.parentElement),
      });
    }, true);
  }, { theme });
  await context.route("**/*", async (route) => {
    const req = route.request();
    const url = req.url();
    if (url.startsWith(origin + "/api/") && !url.startsWith(origin + "/api/auth/callback")) {
      const { status, body } = apiAnswer(url, PAGES[page].session);
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    }
    if (attrNone && req.resourceType() === "document") {
      const res = await route.fetch();
      const headers = { ...res.headers() };
      for (const k of Object.keys(headers)) {
        if (k.toLowerCase() === "content-security-policy") headers[k] = headers[k].replace(/style-src-attr [^;]*/, "style-src-attr 'none'");
      }
      return route.fulfill({ response: res, headers });
    }
    return route.continue();
  });
  const tab = await context.newPage();
  const errors = [];
  tab.on("pageerror", (e) => errors.push(String(e.message).slice(0, 200)));
  const spec = PAGES[page];
  const res = await tab.goto(origin + spec.path, { waitUntil: "load" });
  await tab.waitForTimeout(2500);
  for (const sel of spec.click || []) {
    const el = tab.locator(sel).first();
    if (await el.count()) { await el.click({ timeout: 3000 }).catch(() => {}); await tab.waitForTimeout(1500); break; }
  }
  // Freeze what moves (carets, spinners, the mock transcript's timer) before the picture.
  await tab.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" }).catch(() => {});
  await tab.waitForTimeout(300);
  const violations = await tab.evaluate(() => window.__violations);
  const csp = res ? res.headers()["content-security-policy"] || "" : "";
  return { context, tab, violations, errors, status: res ? res.status() : 0, csp };
}

async function sweep(origin) {
  const pages = (opt("--pages", Object.keys(PAGES).join(","))).split(",");
  const themes = (opt("--themes", "dark,light")).split(",");
  const shots = opt("--shots");
  const attrNone = flag("--attr-none");
  if (shots) mkdirSync(shots, { recursive: true });
  const browser = await launch();
  const report = [];
  let total = 0;
  for (const page of pages) {
    for (const theme of themes) {
      const r = await openPage(browser, origin, page, theme, attrNone);
      if (shots) await r.tab.screenshot({ path: join(shots, `${page}-${theme}.png`), fullPage: true });
      // the policy itself, as served: a page that still admits style attributes fails whatever it drew
      const policyOk = attrNone || /(^|;)\s*style-src-attr 'none'\s*(;|$)/.test(r.csp);
      total += r.violations.length + (policyOk ? 0 : 1);
      report.push({ page, theme, status: r.status, styleSrcAttr: (r.csp.match(/style-src-attr [^;]*/) || [""])[0], violations: r.violations, errors: r.errors });
      console.log(`${r.violations.length || !policyOk ? "FAIL" : "ok  "} ${page.padEnd(12)} ${theme.padEnd(5)} ${r.status} violations=${r.violations.length}${policyOk ? "" : " POLICY-ADMITS-STYLE-ATTRIBUTES"}${r.errors.length ? ` pageerrors=${r.errors.length}` : ""}`);
      for (const v of r.violations) console.log(`       ${v.directive} ${v.element} <${v.parent}> ${JSON.stringify(v.sample)} ${v.source || ""}:${v.line || ""}`);
      await r.context.close();
    }
  }
  await browser.close();
  const json = opt("--json");
  if (json) writeFileSync(json, JSON.stringify(report, null, 2));
  console.log(`${total} violation(s) across ${report.length} page loads`);
  return total === 0;
}

/** Per file in both directories: the share of pixels whose channels differ by more than 8. */
async function compare(dirA, dirB) {
  const browser = await launch();
  const tab = await browser.newPage();
  let worst = 0;
  for (const f of readdirSync(dirA).filter((n) => n.endsWith(".png")).sort()) {
    let b;
    try { b = readFileSync(join(dirB, f)); } catch { console.log(`missing  ${f}`); continue; }
    const a = readFileSync(join(dirA, f));
    const r = await tab.evaluate(async ([a, b]) => {
      const load = (s) => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = s; });
      const [ia, ib] = await Promise.all([load(a), load(b)]);
      if (ia.width !== ib.width || ia.height !== ib.height) return { size: `${ia.width}x${ia.height} vs ${ib.width}x${ib.height}`, diff: 1 };
      const px = (i) => { const c = document.createElement("canvas"); c.width = i.width; c.height = i.height; const x = c.getContext("2d"); x.drawImage(i, 0, 0); return x.getImageData(0, 0, i.width, i.height).data; };
      const da = px(ia), db = px(ib);
      let n = 0;
      for (let k = 0; k < da.length; k += 4) {
        if (Math.abs(da[k] - db[k]) > 8 || Math.abs(da[k + 1] - db[k + 1]) > 8 || Math.abs(da[k + 2] - db[k + 2]) > 8) n++;
      }
      return { size: `${ia.width}x${ia.height}`, diff: n / (da.length / 4) };
    }, [`data:image/png;base64,${a.toString("base64")}`, `data:image/png;base64,${b.toString("base64")}`]);
    worst = Math.max(worst, r.diff);
    console.log(`${(r.diff * 100).toFixed(3).padStart(8)}%  ${r.size.padEnd(22)} ${f}`);
  }
  await browser.close();
  return worst;
}

if (flag("--compare")) {
  const i = args.indexOf("--compare");
  await compare(args[i + 1], args[i + 2]);
} else {
  const origin = args.find((a) => /^https?:\/\//.test(a));
  if (!origin) { console.error("usage: style-attr-sweep.mjs <origin> [--pages …] [--themes …] [--shots dir] [--json out] [--attr-none]"); process.exit(2); }
  process.exit((await sweep(origin.replace(/\/$/, ""))) ? 0 : 1);
}
