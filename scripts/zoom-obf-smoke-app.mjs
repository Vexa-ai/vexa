#!/usr/bin/env node
/**
 * Local-only Zoom OBF/ZAK smoke app.
 *
 * Purpose:
 * - OAuth a Zoom user through a Zoom app you control.
 * - Mint an On-Behalf-Of token for a meeting ID and/or a ZAK token.
 * - Optionally send a Vexa Zoom bot with `zoom_obf_token` or `zoom_zak_token`.
 *
 * No database, no migrations, no npm dependencies. Tokens are held in memory
 * and disappear when this process exits.
 *
 * Run:
 *   node scripts/zoom-obf-smoke-app.mjs
 *
 * Then open:
 *   http://localhost:4173
 */

import crypto from "node:crypto";
import http from "node:http";
import { URL } from "node:url";

const PORT = Number(process.env.PORT || 4173);
const DEFAULT_REDIRECT_URI = `http://localhost:${PORT}/oauth/callback`;
const DEFAULT_VEXA_BASE_URL = process.env.VEXA_BASE_URL || "http://localhost:8056";
const DEFAULT_VEXA_API_KEY = process.env.VEXA_API_KEY || "";

const pendingOAuthStates = new Map();
let zoomConnection = null;
let latestObfToken = null;
let latestZakToken = null;

function htmlEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function maskToken(value) {
  if (!value) return "(empty)";
  const text = String(value);
  if (text.length <= 12) return `${text.slice(0, 3)}...`;
  return `${text.slice(0, 6)}...${text.slice(-6)}`;
}

function normalizeZoomMeetingId(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";

  try {
    const url = new URL(raw.startsWith("http") ? raw : `https://${raw}`);
    const pathMatch = url.pathname.match(/\/(?:j|wc\/join|wc)\/(\d{9,11})/);
    if (pathMatch?.[1]) return pathMatch[1];
  } catch {
    // Not a URL; fall through to digit extraction.
  }

  const compact = raw.replace(/\s+/g, "");
  const exact = compact.match(/^\d{9,11}$/);
  if (exact) return compact;

  const embedded = raw.match(/\b(\d{9,11})\b/);
  return embedded?.[1] || raw;
}

function parseFormBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) {
        req.destroy();
        reject(new Error("Request body too large"));
      }
    });
    req.on("end", () => resolve(Object.fromEntries(new URLSearchParams(body))));
    req.on("error", reject);
  });
}

function sendHtml(res, body, status = 200) {
  res.writeHead(status, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function sendRedirect(res, location) {
  res.writeHead(302, { Location: location });
  res.end();
}

function renderPage({ message = "", error = "", prefill = {} } = {}) {
  const connected = Boolean(zoomConnection?.accessToken);
  const accessToken = prefill.access_token || "";
  const meetingId = prefill.meeting_id || zoomConnection?.meetingId || "";
  const passcode = prefill.passcode || zoomConnection?.passcode || "";
  const vexaBaseUrl = prefill.vexa_base_url || zoomConnection?.vexaBaseUrl || DEFAULT_VEXA_BASE_URL;
  const vexaApiKey = prefill.vexa_api_key || zoomConnection?.vexaApiKey || DEFAULT_VEXA_API_KEY;
  const redirectUri = prefill.redirect_uri || DEFAULT_REDIRECT_URI;

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Zoom OBF Smoke App</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 980px; margin: 32px auto; padding: 0 20px; line-height: 1.45; color: #172033; }
      h1 { margin-bottom: 8px; }
      h2 { margin-top: 28px; border-top: 1px solid #e5e7eb; padding-top: 20px; }
      label { display: block; font-weight: 600; margin-top: 12px; }
      input { width: 100%; box-sizing: border-box; padding: 9px 10px; margin-top: 4px; border: 1px solid #cbd5e1; border-radius: 8px; font: inherit; }
      button { margin-top: 16px; padding: 10px 14px; border: 0; border-radius: 8px; background: #2563eb; color: white; font-weight: 700; cursor: pointer; }
      button.secondary { background: #475569; }
      code, pre { background: #f1f5f9; border-radius: 6px; }
      code { padding: 2px 5px; }
      pre { padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
      .notice { padding: 12px 14px; border-radius: 8px; margin: 16px 0; }
      .ok { background: #ecfdf5; border: 1px solid #a7f3d0; }
      .err { background: #fef2f2; border: 1px solid #fecaca; }
      .warn { background: #fffbeb; border: 1px solid #fde68a; }
      .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
    </style>
  </head>
  <body>
    <h1>Zoom OBF/ZAK Smoke App</h1>
    <p>Local-only helper to mint Zoom OBF/ZAK tokens and send them to Vexa. No DB; tokens live only in this Node process.</p>
    <div class="notice warn">
      <strong>Prereqs:</strong> your Zoom app must have Meeting SDK enabled and <code>user:read:token</code> scope.
      Add <code>user:read:zak</code> if you want to mint ZAK. For a real SDK join, Vexa also needs
      <code>ZOOM_SDK=true</code>, <code>ZOOM_CLIENT_ID</code>, <code>ZOOM_CLIENT_SECRET</code>, and native Zoom SDK binaries.
    </div>
    ${message ? `<div class="notice ok">${htmlEscape(message)}</div>` : ""}
    ${error ? `<div class="notice err">${htmlEscape(error)}</div>` : ""}

    <h2>1. Connect Zoom User OAuth</h2>
    <p>Status: ${connected ? `connected; access token ${htmlEscape(maskToken(zoomConnection.accessToken))}` : "not connected"}</p>
    <form method="post" action="/start-oauth">
      <div class="grid">
        <div>
          <label>Zoom Client ID</label>
          <input name="client_id" required autocomplete="off" value="${htmlEscape(prefill.client_id || "")}" />
        </div>
        <div>
          <label>Zoom Client Secret</label>
          <input name="client_secret" required type="password" autocomplete="off" value="${htmlEscape(prefill.client_secret || "")}" />
        </div>
      </div>
      <label>OAuth Redirect URI</label>
      <input name="redirect_uri" required value="${htmlEscape(redirectUri)}" />
      <label>Meeting ID for later OBF mint</label>
      <input name="meeting_id" placeholder="12345678901" value="${htmlEscape(meetingId)}" />
      <label>Optional passcode</label>
      <input name="passcode" value="${htmlEscape(passcode)}" />
      <label>Vexa API base URL</label>
      <input name="vexa_base_url" value="${htmlEscape(vexaBaseUrl)}" />
      <label>Vexa API key</label>
      <input name="vexa_api_key" type="password" autocomplete="off" value="${htmlEscape(vexaApiKey)}" />
      <button>Start Zoom OAuth</button>
    </form>

    <h2>2. Mint OBF Token</h2>
    <form method="post" action="/mint-obf">
      <label>Meeting ID</label>
      <input name="meeting_id" required value="${htmlEscape(meetingId)}" />
      <label>Optional manual Zoom access token (skips stored OAuth token)</label>
      <input name="access_token" autocomplete="off" value="${htmlEscape(accessToken)}" />
      <button class="secondary">Mint OBF</button>
    </form>
    ${latestObfToken ? `<p>Latest OBF token:</p><pre>${htmlEscape(latestObfToken)}</pre>` : ""}

    <h2>3. Mint ZAK Token</h2>
    <form method="post" action="/mint-zak">
      <label>Optional manual Zoom access token (skips stored OAuth token)</label>
      <input name="access_token" autocomplete="off" value="${htmlEscape(accessToken)}" />
      <button class="secondary">Mint ZAK</button>
    </form>
    ${latestZakToken ? `<p>Latest ZAK token:</p><pre>${htmlEscape(latestZakToken)}</pre>` : ""}

    <h2>4. Send Vexa Bot With OBF or ZAK</h2>
    <form method="post" action="/send-vexa">
      <label>Vexa API base URL</label>
      <input name="vexa_base_url" required value="${htmlEscape(vexaBaseUrl)}" />
      <label>Vexa API key</label>
      <input name="vexa_api_key" required type="password" autocomplete="off" value="${htmlEscape(vexaApiKey)}" />
      <label>Zoom meeting ID</label>
      <input name="meeting_id" required value="${htmlEscape(meetingId)}" />
      <label>Passcode</label>
      <input name="passcode" value="${htmlEscape(passcode)}" />
      <label>OBF token</label>
      <input name="obf_token" autocomplete="off" value="${htmlEscape(latestObfToken || "")}" />
      <label>ZAK token</label>
      <input name="zak_token" autocomplete="off" value="${htmlEscape(latestZakToken || "")}" />
      <label>Bot name</label>
      <input name="bot_name" value="Vexa OBF Test" />
      <button>Send Vexa Bot</button>
    </form>
  </body>
</html>`;
}

async function exchangeAuthorizationCode({ code, redirectUri, clientId, clientSecret }) {
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
  });
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const response = await fetch(`https://zoom.us/oauth/token?${params}`, {
    method: "POST",
    headers: { Authorization: `Basic ${basic}` },
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Zoom token exchange failed (${response.status}): ${text}`);
  }
  return JSON.parse(text);
}

async function refreshZoomTokenIfNeeded() {
  if (!zoomConnection?.refreshToken || !zoomConnection?.clientId || !zoomConnection?.clientSecret) {
    return;
  }
  if (Date.now() < zoomConnection.expiresAtMs - 60_000) {
    return;
  }
  const params = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: zoomConnection.refreshToken,
  });
  const basic = Buffer.from(`${zoomConnection.clientId}:${zoomConnection.clientSecret}`).toString("base64");
  const response = await fetch(`https://zoom.us/oauth/token?${params}`, {
    method: "POST",
    headers: { Authorization: `Basic ${basic}` },
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Zoom refresh failed (${response.status}): ${text}`);
  }
  const token = JSON.parse(text);
  zoomConnection.accessToken = token.access_token;
  if (token.refresh_token) zoomConnection.refreshToken = token.refresh_token;
  zoomConnection.expiresAtMs = Date.now() + Number(token.expires_in || 3600) * 1000;
}

async function mintObfToken({ meetingId, accessToken }) {
  const normalizedMeetingId = normalizeZoomMeetingId(meetingId);
  const response = await fetch(
    `https://api.zoom.us/v2/users/me/token?type=onbehalf&meeting_id=${encodeURIComponent(normalizedMeetingId)}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`OBF mint failed (${response.status}): ${text}`);
  }
  const body = JSON.parse(text);
  if (!body.token) {
    throw new Error(`OBF mint response did not include token: ${text}`);
  }
  return body.token;
}

async function mintZakToken({ accessToken }) {
  const response = await fetch(
    "https://api.zoom.us/v2/users/me/token?type=zak",
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`ZAK mint failed (${response.status}): ${text}`);
  }
  const body = JSON.parse(text);
  if (!body.token) {
    throw new Error(`ZAK mint response did not include token: ${text}`);
  }
  return body.token;
}

async function sendVexaBot({ vexaBaseUrl, vexaApiKey, meetingId, passcode, obfToken, zakToken, botName }) {
  const normalizedMeetingId = normalizeZoomMeetingId(meetingId);
  const payload = {
    platform: "zoom",
    native_meeting_id: normalizedMeetingId,
    bot_name: botName || "Vexa OBF Test",
    transcribe_enabled: false,
    recording_enabled: false,
  };
  if (obfToken) payload.zoom_obf_token = obfToken;
  if (zakToken) payload.zoom_zak_token = zakToken;
  if (passcode) payload.passcode = passcode;
  if (!payload.zoom_obf_token && !payload.zoom_zak_token) {
    throw new Error("Provide at least one token: OBF or ZAK.");
  }

  const response = await fetch(`${vexaBaseUrl.replace(/\/$/, "")}/bots`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": vexaApiKey,
    },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Vexa bot create failed (${response.status}): ${text}\nPayload: ${JSON.stringify(payload)}`);
  }
  return text;
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || `localhost:${PORT}`}`);

    if (req.method === "GET" && url.pathname === "/") {
      sendHtml(res, renderPage());
      return;
    }

    if (req.method === "POST" && url.pathname === "/start-oauth") {
      const form = await parseFormBody(req);
      const state = crypto.randomBytes(16).toString("hex");
      pendingOAuthStates.set(state, {
        clientId: form.client_id,
        clientSecret: form.client_secret,
        redirectUri: form.redirect_uri || DEFAULT_REDIRECT_URI,
        meetingId: form.meeting_id,
        passcode: form.passcode,
        vexaBaseUrl: form.vexa_base_url || DEFAULT_VEXA_BASE_URL,
        vexaApiKey: form.vexa_api_key || DEFAULT_VEXA_API_KEY,
        createdAtMs: Date.now(),
      });
      const authUrl = new URL("https://zoom.us/oauth/authorize");
      authUrl.searchParams.set("response_type", "code");
      authUrl.searchParams.set("client_id", form.client_id);
      authUrl.searchParams.set("redirect_uri", form.redirect_uri || DEFAULT_REDIRECT_URI);
      authUrl.searchParams.set("state", state);
      sendRedirect(res, authUrl.toString());
      return;
    }

    if (req.method === "GET" && url.pathname === "/oauth/callback") {
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      const pending = state ? pendingOAuthStates.get(state) : null;
      if (!code || !state || !pending) {
        sendHtml(res, renderPage({ error: "Missing or invalid OAuth code/state." }), 400);
        return;
      }
      pendingOAuthStates.delete(state);
      const token = await exchangeAuthorizationCode({
        code,
        redirectUri: pending.redirectUri,
        clientId: pending.clientId,
        clientSecret: pending.clientSecret,
      });
      zoomConnection = {
        accessToken: token.access_token,
        refreshToken: token.refresh_token,
        expiresAtMs: Date.now() + Number(token.expires_in || 3600) * 1000,
        scope: token.scope,
        clientId: pending.clientId,
        clientSecret: pending.clientSecret,
        meetingId: pending.meetingId,
        passcode: pending.passcode,
        vexaBaseUrl: pending.vexaBaseUrl,
        vexaApiKey: pending.vexaApiKey,
      };
      sendHtml(res, renderPage({ message: "Zoom OAuth connected. You can mint an OBF token now." }));
      return;
    }

    if (req.method === "POST" && url.pathname === "/mint-obf") {
      const form = await parseFormBody(req);
      const meetingId = normalizeZoomMeetingId(form.meeting_id || zoomConnection?.meetingId);
      if (!meetingId) throw new Error("Meeting ID is required.");
      let accessToken = form.access_token || zoomConnection?.accessToken;
      if (!form.access_token) {
        await refreshZoomTokenIfNeeded();
        accessToken = zoomConnection?.accessToken;
      }
      if (!accessToken) throw new Error("Connect Zoom OAuth or paste an access token first.");
      latestObfToken = await mintObfToken({ meetingId, accessToken });
      if (zoomConnection) zoomConnection.meetingId = meetingId;
      sendHtml(res, renderPage({ message: "OBF token minted.", prefill: { meeting_id: meetingId } }));
      return;
    }

    if (req.method === "POST" && url.pathname === "/mint-zak") {
      const form = await parseFormBody(req);
      let accessToken = form.access_token || zoomConnection?.accessToken;
      if (!form.access_token) {
        await refreshZoomTokenIfNeeded();
        accessToken = zoomConnection?.accessToken;
      }
      if (!accessToken) throw new Error("Connect Zoom OAuth or paste an access token first.");
      latestZakToken = await mintZakToken({ accessToken });
      sendHtml(res, renderPage({ message: "ZAK token minted." }));
      return;
    }

    if (req.method === "POST" && url.pathname === "/send-vexa") {
      const form = await parseFormBody(req);
      const meetingId = normalizeZoomMeetingId(form.meeting_id);
      const body = await sendVexaBot({
        vexaBaseUrl: form.vexa_base_url || DEFAULT_VEXA_BASE_URL,
        vexaApiKey: form.vexa_api_key || DEFAULT_VEXA_API_KEY,
        meetingId,
        passcode: form.passcode,
        obfToken: form.obf_token,
        zakToken: form.zak_token,
        botName: form.bot_name,
      });
      sendHtml(res, renderPage({
        message: `Vexa bot request succeeded: ${body}`,
        prefill: {
          meeting_id: meetingId,
          passcode: form.passcode,
          vexa_base_url: form.vexa_base_url,
          vexa_api_key: form.vexa_api_key,
        },
      }));
      return;
    }

    sendHtml(res, renderPage({ error: `Route not found: ${url.pathname}` }), 404);
  } catch (error) {
    sendHtml(res, renderPage({ error: error?.message || String(error) }), 500);
  }
});

server.listen(PORT, () => {
  console.log(`Zoom OBF smoke app running at http://localhost:${PORT}`);
  console.log(`Use redirect URI: ${DEFAULT_REDIRECT_URI}`);
});
