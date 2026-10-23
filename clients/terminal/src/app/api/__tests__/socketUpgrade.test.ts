// @vitest-environment node
/** The relayed browser sockets (server.mjs) are taken only from this terminal's own pages, and only
 *  with the person's session cookie — a voice note never on a deployment key, `/ws` only when the
 *  deployment switched zero-login on. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { originAllowed, refuseUpgrade, upgradeRefusal, upstreamKey } from "../socketUpgrade.mjs";

const SERVER = fileURLToPath(new URL("../../../../server.mjs", import.meta.url));
const COOKIE = "vexa-token";
const VOICE = "/voice-notes/vn-0123456789ab/stream";
const ENV = { NEXTAUTH_URL: "https://minutes.example.test", VEXA_API_KEY: "deployment-key" };

function req(headers: Record<string, string>) {
  return { headers: { host: "terminal.internal:3000", ...headers } };
}

describe("the Origin of a relayed socket", () => {
  it("takes the same origin and the configured public origin", () => {
    expect(originAllowed({ host: "terminal.internal:3000", origin: "http://terminal.internal:3000" }, ENV)).toBe(true);
    expect(originAllowed({ host: "terminal.internal:3000", origin: "https://minutes.example.test" }, ENV)).toBe(true);
    expect(originAllowed({ host: "terminal.internal:3000" }, ENV)).toBe(true); // not a browser
  });

  it.each([
    ["a foreign site", "https://elsewhere.example"],
    ["a sibling subdomain", "https://notes.minutes.example.test"],
    ["the public host on another port", "https://minutes.example.test:8443"],
    ["an opaque origin", "null"],
  ])("refuses %s", (_label, origin) => {
    for (const path of ["/ws", VOICE]) {
      expect(upgradeRefusal(req({ origin, cookie: `${COOKIE}=user-token` }), path, ENV, COOKIE))
        .toEqual({ status: 403, reason: "Forbidden" });
    }
  });
});

describe("the key of a relayed socket", () => {
  it("refuses a voice-note upgrade without the session cookie", () => {
    expect(upgradeRefusal(req({ origin: "https://minutes.example.test" }), VOICE, ENV, COOKIE))
      .toEqual({ status: 401, reason: "Unauthorized" });
    expect(upstreamKey(req({}), VOICE, ENV, COOKIE)).toBe("");
    expect(upstreamKey(req({}), VOICE, { VEXA_BOT_API_KEY: "bot-key" }, COOKIE)).toBe("");
  });

  it("sends the session's own key for a voice note", () => {
    const r = req({ origin: "https://minutes.example.test", cookie: `other=1; ${COOKIE}=user-token` });
    expect(upgradeRefusal(r, VOICE, ENV, COOKIE)).toBeNull();
    expect(upstreamKey(r, VOICE, ENV, COOKIE)).toBe("user-token");
  });

  it("refuses an anonymous /ws upgrade: a deployment key set is not a session", () => {
    expect(upgradeRefusal(req({}), "/ws", ENV, COOKIE)).toEqual({ status: 401, reason: "Unauthorized" });
    expect(upstreamKey(req({}), "/ws", ENV, COOKIE)).toBe("");
    expect(upstreamKey(req({}), "/ws", { VEXA_BOT_API_KEY: "bot-key" }, COOKIE)).toBe("");
    expect(upstreamKey(req({}), "/ws", { ...ENV, VEXA_TERMINAL_ZERO_LOGIN: "1" }, COOKIE)).toBe("");
  });

  it("acts as the deployment key on /ws only when zero-login is switched on", () => {
    const zero = { ...ENV, VEXA_TERMINAL_ZERO_LOGIN: "true" };
    expect(upgradeRefusal(req({}), "/ws", zero, COOKIE)).toBeNull();
    expect(upstreamKey(req({}), "/ws", zero, COOKIE)).toBe("deployment-key");
    // …and never for a voice note
    expect(upstreamKey(req({}), VOICE, zero, COOKIE)).toBe("");
  });

  it("answers a refusal on the raw socket and closes it", () => {
    const written: string[] = [];
    let destroyed = false;
    refuseUpgrade({ write: (s: string) => written.push(s), destroy: () => { destroyed = true; } },
      { status: 403, reason: "Forbidden" });
    expect(written[0]).toMatch(/^HTTP\/1\.1 403 Forbidden\r\n/);
    expect(destroyed).toBe(true);
  });
});

describe("server.mjs", () => {
  const source = readFileSync(SERVER, "utf8");
  it("checks every relayed upgrade before taking it, and keys it per path", () => {
    expect(source).toMatch(/upgradeRefusal\(req, upstreamPath, process\.env, AUTH_COOKIE\)/);
    expect(source).toMatch(/refuseUpgrade\(socket, refusal\)/);
    expect(source).toMatch(/upstreamKey\(req, upstreamPath, process\.env, AUTH_COOKIE\)/);
    expect(source).not.toMatch(/function resolveUpstreamKey/);
  });
});
