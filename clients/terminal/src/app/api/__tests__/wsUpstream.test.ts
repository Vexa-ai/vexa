// @vitest-environment node
/** server.mjs proxies two WebSocket shapes to the gateway with the per-user key: `/ws` and a browser
 *  session's live view. Other `/bot-accounts/` shapes are refused; HMR and the rest stay with Next. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { wsUpstream } from "../wsUpstream.mjs";

const SERVER = fileURLToPath(new URL("../../../../server.mjs", import.meta.url));
const ID = "0123456789abcdef0123456789abcdef";

describe("the WebSocket upgrade matcher", () => {
  it("proxies /ws to the gateway's /ws", () => {
    expect(wsUpstream("/ws")).toEqual({ path: "/ws" });
  });

  it("proxies a voice note's audio socket to the same gateway path", () => {
    expect(wsUpstream("/voice-notes/note_1/stream")).toEqual({ path: "/voice-notes/note_1/stream" });
  });

  it("proxies a session's live view to the same gateway path", () => {
    expect(wsUpstream(`/bot-accounts/sessions/${ID}/vnc`)).toEqual({ path: `/bot-accounts/sessions/${ID}/vnc` });
  });

  it.each([
    "/bot-accounts/sessions/ABCDEF0123456789abcdef0123456789/vnc",
    "/bot-accounts/sessions/abc/vnc",
    `/bot-accounts/sessions/${ID}/vnc/extra`,
    `/bot-accounts/sessions/${ID}`,
    `/bot-accounts/sessions/${ID}/../../ws`,
    "/bot-accounts",
  ])("refuses %s", (path) => {
    expect(wsUpstream(path)).toEqual({ refuse: true });
  });

  it("leaves Next's own upgrades alone", () => {
    expect(wsUpstream("/_next/webpack-hmr")).toBeNull();
    expect(wsUpstream("/ws/x")).toBeNull();
    expect(wsUpstream("/")).toBeNull();
  });

  it("is what server.mjs routes the upgrade by, with the same key resolution for both shapes", () => {
    const src = readFileSync(SERVER, "utf8");
    const upgrade = src.slice(src.indexOf('server.on("upgrade"'), src.indexOf('server.on("clientError"'));
    expect(upgrade).toContain("wsUpstream(pathname)");
    expect(upgrade).toContain("upstreamKey(req, upstreamPath, process.env, AUTH_COOKIE)");
    expect(upgrade).toContain("routed.path");
    expect(src).toContain("const target = `${GATEWAY_URL}${upstreamPath}`;");
  });
});
