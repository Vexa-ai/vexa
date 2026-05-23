#!/usr/bin/env node
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "../../../..");
const requireFromDashboard = createRequire(path.join(ROOT, "services/dashboard/package.json"));
const { chromium } = requireFromDashboard("playwright");

function usage() {
  console.error(`Usage:
  dashboard-ws-frame-proof.mjs --dashboard-url URL --meeting-id ID --out FILE [options]

Options:
  --platform NAME          Expected platform, used only for validation metadata.
  --native-id ID           Native meeting id. Required for --from-list row selection.
  --from-list              Navigate to /meetings and click the matching row instead of direct /meetings/<id>.
  --auth-cookie-name NAME  Dashboard auth cookie name. Defaults to /api/config authCookieName, then vexa-token.
  --auth-token TOKEN       Validation API token. Defaults to DASHBOARD_AUTH_TOKEN, then /api/config authToken.
  --timeout-ms MS          Default 30000.
  --legacy-native-only     Accept the old 0.10.6 dashboard subscription shape that sends platform/native_id only.
  --expect-text TEXT       Require at least one transcript WS frame and rendered body text containing this text.
  --allow-no-transcript    Pass if subscribe/ack is observed even without a transcript frame.
  --headed                 Run a headed browser.
`);
}

const args = process.argv.slice(2);
const opts = {
  timeoutMs: 30000,
  fromList: false,
  allowNoTranscript: false,
  headed: false,
  authCookieName: null,
  legacyNativeOnly: false,
  expectText: null,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === "--dashboard-url") opts.dashboardUrl = args[++i];
  else if (arg === "--meeting-id") opts.meetingId = args[++i];
  else if (arg === "--platform") opts.platform = args[++i];
  else if (arg === "--native-id") opts.nativeId = args[++i];
  else if (arg === "--out") opts.out = args[++i];
  else if (arg === "--auth-cookie-name") opts.authCookieName = args[++i];
  else if (arg === "--auth-token") opts.authToken = args[++i];
  else if (arg === "--timeout-ms") opts.timeoutMs = Number(args[++i]);
  else if (arg === "--legacy-native-only") opts.legacyNativeOnly = true;
  else if (arg === "--expect-text") opts.expectText = args[++i];
  else if (arg === "--from-list") opts.fromList = true;
  else if (arg === "--allow-no-transcript") opts.allowNoTranscript = true;
  else if (arg === "--headed") opts.headed = true;
  else if (arg === "-h" || arg === "--help") {
    usage();
    process.exit(0);
  } else {
    console.error(`Unknown argument: ${arg}`);
    usage();
    process.exit(2);
  }
}

if (!opts.dashboardUrl || !opts.meetingId || !opts.out) {
  usage();
  process.exit(2);
}
if (opts.fromList && !opts.nativeId) {
  console.error("--native-id is required with --from-list");
  process.exit(2);
}

opts.dashboardUrl = opts.dashboardUrl.replace(/\/+$/, "");

function redact(value) {
  return String(value ?? "")
    .replace(/api_key=[^&\s"]+/g, "api_key=***")
    .replace(/"authToken"\s*:\s*"[^"]+"/g, '"authToken":"***"');
}

async function fetchJson(url) {
  const response = await fetch(url);
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = { raw: text };
  }
  return { status: response.status, json };
}

function authState(token) {
  return JSON.stringify({
    state: {
      user: { id: 1, email: "test@vexa.ai", name: "test@vexa.ai" },
      token,
      isAuthenticated: true,
      didLogout: false,
    },
    version: 0,
  });
}

function frameIncludesMeeting(event, kind, opts) {
  if (!event.payload) return false;
  if (kind === "subscribe") {
    if (!event.payload.includes("\"action\":\"subscribe\"")) return false;
    if (!opts.legacyNativeOnly && event.payload.includes(`"meeting_id":"${opts.meetingId}"`)) return true;
    return Boolean(
      opts.legacyNativeOnly &&
        opts.platform &&
        opts.nativeId &&
        event.payload.includes(`"platform":"${opts.platform}"`) &&
        event.payload.includes(`"native_id":"${opts.nativeId}"`)
    );
  }
  if (kind === "ack") {
    if (!event.payload.includes("\"type\": \"subscribed\"") && !event.payload.includes("\"type\":\"subscribed\"")) return false;
    if (!opts.legacyNativeOnly && event.payload.includes(`"meeting_id": "${opts.meetingId}"`)) return true;
    return Boolean(
      opts.legacyNativeOnly &&
        opts.platform &&
        opts.nativeId &&
        event.payload.includes(`"platform": "${opts.platform}"`) &&
        event.payload.includes(`"native_id": "${opts.nativeId}"`)
    );
  }
  if (kind === "transcript") {
    if (!event.payload.includes("\"type\":\"transcript\"") && !event.payload.includes("\"type\": \"transcript\"")) return false;
    if (opts.expectText && !event.payload.includes(opts.expectText)) return false;
    if (event.payload.includes(`"id":${opts.meetingId}`) || event.payload.includes(`"id": ${opts.meetingId}`)) return true;
    return Boolean(opts.legacyNativeOnly && opts.expectText);
  }
  return false;
}

const startedAt = new Date().toISOString();
const config = await fetchJson(`${opts.dashboardUrl}/api/config`);
const token = opts.authToken || process.env.DASHBOARD_AUTH_TOKEN || config.json?.authToken || null;
if (!token) {
  throw new Error(`${opts.dashboardUrl}/api/config did not expose an authToken and DASHBOARD_AUTH_TOKEN was not set`);
}
opts.authCookieName = opts.authCookieName || config.json?.authCookieName || "vexa-token";

const browser = await chromium.launch({ headless: !opts.headed });
const context = await browser.newContext();
await context.addCookies([
  {
    name: opts.authCookieName,
    value: token,
    url: opts.dashboardUrl,
    sameSite: "Lax",
  },
]);
await context.addInitScript((state) => {
  window.localStorage.setItem("vexa-auth", state);
}, authState(token));

const page = await context.newPage();
const client = await context.newCDPSession(page);
const events = [];

await client.send("Network.enable");
client.on("Network.webSocketCreated", (event) => {
  events.push({
    t: new Date().toISOString(),
    event: "created",
    requestId: event.requestId,
    url: redact(event.url),
  });
});
client.on("Network.webSocketFrameSent", (event) => {
  events.push({
    t: new Date().toISOString(),
    event: "frame-sent",
    requestId: event.requestId,
    payload: redact(event.response?.payloadData).slice(0, 2000),
  });
});
client.on("Network.webSocketFrameReceived", (event) => {
  events.push({
    t: new Date().toISOString(),
    event: "frame-received",
    requestId: event.requestId,
    payload: redact(event.response?.payloadData).slice(0, 3000),
  });
});
client.on("Network.webSocketClosed", (event) => {
  events.push({
    t: new Date().toISOString(),
    event: "closed",
    requestId: event.requestId,
  });
});

let navigation = {};
let exitCode = 1;
try {
  if (opts.fromList) {
    await page.goto(`${opts.dashboardUrl}/meetings`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("tr", { timeout: opts.timeoutMs });
    const detailLink = page.locator(`a[href$="/meetings/${opts.meetingId}"]`).first();
    try {
      await detailLink.waitFor({ timeout: Math.min(5000, opts.timeoutMs) });
      await detailLink.click();
      navigation = { mode: "from-list-link-click", nativeId: opts.nativeId };
    } catch {
      const row = page.locator("tr").filter({ hasText: opts.nativeId }).first();
      await row.waitFor({ timeout: opts.timeoutMs });
      await row.click();
      navigation = { mode: "from-list-row-click", nativeId: opts.nativeId };
    }
    await page.waitForURL(new RegExp(`/meetings/${opts.meetingId}$`), { timeout: opts.timeoutMs });
  } else {
    await page.goto(`${opts.dashboardUrl}/meetings/${opts.meetingId}`, { waitUntil: "domcontentloaded" });
    navigation = { mode: "direct" };
  }

  const deadline = Date.now() + opts.timeoutMs;
  while (Date.now() < deadline) {
    const hasSubscribe = events.some((event) => event.event === "frame-sent" && frameIncludesMeeting(event, "subscribe", opts));
    const hasAck = events.some((event) => event.event === "frame-received" && frameIncludesMeeting(event, "ack", opts));
    const hasTranscript = events.some((event) => event.event === "frame-received" && frameIncludesMeeting(event, "transcript", opts));
    if (hasSubscribe && hasAck && (hasTranscript || opts.allowNoTranscript)) break;
    await page.waitForTimeout(500);
  }

  const bodyText = await page.locator("body").innerText().catch(() => "");
  const sentSubscribeFrames = events.filter((event) => event.event === "frame-sent" && frameIncludesMeeting(event, "subscribe", opts));
  const subscribedAckFrames = events.filter((event) => event.event === "frame-received" && frameIncludesMeeting(event, "ack", opts));
  const transcriptFrames = events.filter((event) => event.event === "frame-received" && frameIncludesMeeting(event, "transcript", opts));
  const expectedTextRendered = opts.expectText ? bodyText.includes(opts.expectText) : null;
  const success =
    sentSubscribeFrames.length > 0 &&
    subscribedAckFrames.length > 0 &&
    (transcriptFrames.length > 0 || opts.allowNoTranscript) &&
    (expectedTextRendered !== false);

  const result = {
    started_at: startedAt,
    ended_at: new Date().toISOString(),
    dashboard_url: opts.dashboardUrl,
    final_url: page.url(),
    meeting_id: String(opts.meetingId),
    platform: opts.platform || null,
    native_id: opts.nativeId || null,
    legacy_native_only: opts.legacyNativeOnly,
    expected_text: opts.expectText || null,
    navigation,
    config: {
      status: config.status,
      apiUrl: config.json?.apiUrl || null,
      publicApiUrl: config.json?.publicApiUrl || null,
      wsUrl: config.json?.wsUrl || null,
      authToken_present: Boolean(token),
      authCookieName: opts.authCookieName,
    },
    success,
    exit_code: success ? 0 : 1,
    require_transcript_frame: !opts.allowNoTranscript,
    frame_counts: {
      created: events.filter((event) => event.event === "created").length,
      subscribe_sent_for_meeting: sentSubscribeFrames.length,
      subscribed_ack_for_meeting: subscribedAckFrames.length,
      transcript_received_for_meeting: transcriptFrames.length,
      closed: events.filter((event) => event.event === "closed").length,
      total: events.length,
    },
    created_frames: events.filter((event) => event.event === "created"),
    sent_subscribe_frames: sentSubscribeFrames,
    subscribed_ack_frames: subscribedAckFrames,
    transcript_frames: transcriptFrames.slice(0, 5),
    expected_text_rendered: expectedTextRendered,
    visible_transcript_hint: /Transcript|Speakers/i.test(bodyText),
    body_excerpt: bodyText.slice(0, 1200),
    note: "Frame-level proof. DOM transcript text alone does not count as dashboard WS delivery.",
  };

  fs.mkdirSync(path.dirname(opts.out), { recursive: true });
  fs.writeFileSync(opts.out, JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result, null, 2));
  exitCode = result.exit_code;
} finally {
  try {
    await browser.close();
  } catch (error) {
    if (exitCode !== 0) throw error;
  }
}
process.exit(exitCode);
