/**
 * L2 — pins resolveBrowserConfig to the /api/config decisions. Exit 1 on any failure.
 *
 * The cases that matter (and the regressions they guard):
 *   1. both-loopback + configured port == gatewayHostPort → gateway-DIRECT: keep the configured URL,
 *      derive the matching ws:// wsUrl (Learning #37 — the App Router can't upgrade a same-origin /ws,
 *      so the tunneled gateway port must be trusted).
 *   2. both-loopback + configured port != gatewayHostPort → same-origin: apiUrl "" AND wsUrl "".
 *   3. configured loopback URL but a PUBLIC request host → rewrite the host onto the public name,
 *      wsUrl follows the resolved base.
 *   4. https base → wss wsUrl.
 *   5. internal service URL (api-gateway) with no configured public URL → same-origin "".
 *   6. authToken: cookie wins over selfHostKey; selfHostKey when no cookie; null when neither.
 */
import { resolveBrowserConfig, isLoopbackHost } from "./index.ts";
import type { ResolveBrowserConfigInput, BrowserConfig } from "./index.ts";

let failed = 0;
const ok = (label: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond ? "" : detail ? " — " + detail : ""}`);
  if (!cond) failed++;
};

const eq = (label: string, got: unknown, want: unknown) =>
  ok(label, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

const base = (over: Partial<ResolveBrowserConfigInput> = {}): ResolveBrowserConfigInput => ({
  internalApiUrl: "http://api-gateway:8056",
  requestHost: "127.0.0.1:18030",
  requestProto: "http",
  ...over,
});

// ── 1) both-loopback, configured port == gatewayHostPort → gateway-direct ─────────────────────────
{
  const cfg = resolveBrowserConfig(base({
    configuredPublicApiUrl: "http://127.0.0.1:18056",
    requestHost: "127.0.0.1:18030", // request via the SSH tunnel
    gatewayHostPort: "18056",
  }));
  eq("1a both-loopback gateway-direct keeps configured apiUrl", cfg.apiUrl, "http://127.0.0.1:18056");
  eq("1b both-loopback gateway-direct derives ws:// wsUrl", cfg.wsUrl, "ws://127.0.0.1:18056/ws");
}

// ── 2) both-loopback, configured port != gatewayHostPort → same-origin ────────────────────────────
{
  const cfg = resolveBrowserConfig(base({
    configuredPublicApiUrl: "http://127.0.0.1:8056", // container-internal, NOT the published port
    requestHost: "127.0.0.1:18030",
    gatewayHostPort: "18056",
  }));
  eq("2a both-loopback non-gateway-port apiUrl is same-origin", cfg.apiUrl, "");
  eq("2b both-loopback non-gateway-port wsUrl is same-origin", cfg.wsUrl, "");
}

// also: both-loopback with NO gatewayHostPort configured → same-origin
{
  const cfg = resolveBrowserConfig(base({
    configuredPublicApiUrl: "http://localhost:18056",
    requestHost: "localhost:18030",
  }));
  eq("2c both-loopback, no gatewayHostPort → same-origin apiUrl", cfg.apiUrl, "");
  eq("2d both-loopback, no gatewayHostPort → same-origin wsUrl", cfg.wsUrl, "");
}

// ── 3) configured loopback URL but a PUBLIC request host → rewrite onto the public host ───────────
{
  const cfg = resolveBrowserConfig(base({
    configuredPublicApiUrl: "http://127.0.0.1:18056",
    requestHost: "app.example.com",
    requestProto: "http",
    gatewayHostPort: "18056",
  }));
  eq("3a loopback-configured + public host → rewrite apiUrl", cfg.apiUrl, "http://app.example.com:18056");
  eq("3b rewritten apiUrl → matching wsUrl", cfg.wsUrl, "ws://app.example.com:18056/ws");
}

// ── 4) https configured base → wss wsUrl ──────────────────────────────────────────────────────────
{
  const cfg = resolveBrowserConfig(base({
    configuredPublicApiUrl: "https://api.vexa.ai",
    requestHost: "app.vexa.ai",
    requestProto: "https",
  }));
  eq("4a https base kept as apiUrl", cfg.apiUrl, "https://api.vexa.ai");
  eq("4b https base → wss wsUrl", cfg.wsUrl, "wss://api.vexa.ai/ws");
}

// ── 5) internal service URL, no configured public URL → same-origin ───────────────────────────────
{
  const cfg = resolveBrowserConfig(base({
    internalApiUrl: "http://api-gateway:8056",
    configuredPublicApiUrl: "",
    requestHost: "app.example.com",
  }));
  eq("5a internal-service apiUrl is same-origin", cfg.apiUrl, "");
  eq("5b internal-service wsUrl is same-origin", cfg.wsUrl, "");
}

// ── 6) authToken: ONE source, cookie || selfHostKey || null ───────────────────────────────────────
{
  const cookie = resolveBrowserConfig(base({ cookieToken: "cookie-tok", selfHostKey: "self-tok" }));
  eq("6a authToken: cookie wins over selfHostKey", cookie.authToken, "cookie-tok");

  const self = resolveBrowserConfig(base({ cookieToken: null, selfHostKey: "self-tok" }));
  eq("6b authToken: selfHostKey when no cookie", self.authToken, "self-tok");

  const none = resolveBrowserConfig(base({}));
  eq("6c authToken: null when neither", none.authToken, null);

  const emptyCookie = resolveBrowserConfig(base({ cookieToken: "", selfHostKey: "self-tok" }));
  eq("6d authToken: empty cookie falls through to selfHostKey", emptyCookie.authToken, "self-tok");
}

// ── 7) the exported type + helper are usable (compile-time + runtime sanity) ──────────────────────
{
  const cfg: BrowserConfig = resolveBrowserConfig(base());
  ok("7a returns the three fields", "apiUrl" in cfg && "wsUrl" in cfg && "authToken" in cfg);
  ok("7b isLoopbackHost is exported and works", isLoopbackHost("127.0.0.1") && !isLoopbackHost("app.example.com"));
}

console.log(failed === 0 ? "\nPASS dash-config" : `\nFAIL dash-config (${failed} failed)`);
process.exit(failed === 0 ? 0 : 1);
