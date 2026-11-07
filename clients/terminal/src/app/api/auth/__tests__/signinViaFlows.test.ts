/** The sign-in link goes through the ONE outbound mail path (flows `POST /mail/sign-in`), so the
 *  global account's `sign-in` template, sender and audit apply — and it NEVER depends on it: flows
 *  absent, refusing or failing, the terminal sends its own baked mail. Plus the relay switch:
 *  `capture` dials only the capture double, `relay` never relays a test domain. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MailArgs = { to: string; subject: string; text: string };
const sendMail = vi.fn(async (_opts: MailArgs): Promise<void> => {});
vi.mock("../mailer", async (orig) => ({
  ...(await orig<typeof import("../mailer")>()),
  sendMail: (opts: MailArgs) => sendMail(opts),
}));

import { startLinkDelivery, _settleLinkDeliveries } from "../linkDelivery";
import { mailerConfig, isTestDomain } from "../mailer";

const calls: string[] = [];
function fetchAnswering(flowsStatus: number | "throw") {
  return vi.fn(async (url: string) => {
    calls.push(String(url));
    if (String(url).includes("/internal/signin-admission"))
      return new Response(JSON.stringify({ admitted: true, why: "allow-list" }), { status: 200 });
    if (String(url).includes("/mail/sign-in")) {
      if (flowsStatus === "throw") throw new TypeError("fetch failed");
      return new Response("{}", { status: flowsStatus });
    }
    return new Response("nope", { status: 500 });
  });
}

beforeEach(() => {
  calls.length = 0;
  sendMail.mockClear();
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("VEXA_FLOWS_API_URL", "http://flows-api:8200");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("sign-in link through the one outbound path", () => {
  it("is sent by flows when flows answers, and not twice", async () => {
    vi.stubGlobal("fetch", fetchAnswering(200));
    startLinkDelivery("anna@bank.com", "https://t/redeem?t=x", 15);
    await _settleLinkDeliveries();
    expect(calls.some((u) => u === "http://flows-api:8200/mail/sign-in")).toBe(true);
    expect(sendMail).not.toHaveBeenCalled();
  });

  it.each([500, 403, "throw" as const])("falls back to the baked mail when flows answers %s", async (st) => {
    vi.stubGlobal("fetch", fetchAnswering(st));
    startLinkDelivery("anna@bank.com", "https://t/redeem?t=x", 15);
    await _settleLinkDeliveries();
    expect(sendMail).toHaveBeenCalledTimes(1);
    expect(sendMail.mock.calls[0][0].text).toContain("https://t/redeem?t=x");
  });

  it("does not ask flows when it is not configured", async () => {
    vi.stubEnv("VEXA_FLOWS_API_URL", "");
    vi.stubGlobal("fetch", fetchAnswering(200));
    startLinkDelivery("anna@bank.com", "https://t/redeem?t=x", 15);
    await _settleLinkDeliveries();
    expect(calls.some((u) => u.includes("/mail/sign-in"))).toBe(false);
    expect(sendMail).toHaveBeenCalledTimes(1);
  });
});

describe("the relay switch", () => {
  it("capture dials only the capture double, whatever the relay says", () => {
    vi.stubEnv("VEXA_MAIL_RELAY", "capture");
    vi.stubEnv("VEXA_MAIL_CAPTURE_SMTP", "mailpit:1025");
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "smtp.gmail.com");
    vi.stubEnv("VEXA_MAIL_SMTP_USER", "info@vexa.ai");
    const cfg = mailerConfig();
    expect([cfg.host, cfg.port, cfg.user, cfg.secure]).toEqual(["mailpit", 1025, undefined, false]);
  });

  it("knows a test domain", () => {
    for (const a of ["a@bank.test", "b@example.com", "c@x.invalid", "Vexa <d@x.localhost>"]) expect(isTestDomain(a)).toBe(true);
    for (const a of ["a@vexa.ai", "b@gmail.com"]) expect(isTestDomain(a)).toBe(false);
  });

  it("carries the Reply-To", () => {
    vi.stubEnv("VEXA_MAIL_REPLY_TO", "info@vexa.ai");
    expect(mailerConfig().replyTo).toBe("info@vexa.ai");
  });
});
