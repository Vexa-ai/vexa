/** The sign-in mail's settings are the deployment's mail family, VEXA_MAIL_SMTP_* (S2).
 *
 *  The terminal used to read seven unprefixed SMTP_* keys that no contract declared, next to the
 *  VEXA_MAIL_SMTP_* family flows already sends through. It now reads the family (declared in
 *  clients/terminal/config.v1.json, held on compose, Helm and Lite by gate:config-contract) and
 *  nothing else: an old SMTP_* name configures nothing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mailerConfig, recipientAllowed, sendMail } from "../mailer";

const FAMILY = ["HOST", "PORT", "FROM", "USER", "PASSWORD", "SECURE", "TLS_INSECURE"].map((k) => `VEXA_MAIL_SMTP_${k}`);
const LEGACY = ["SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USER", "SMTP_PASS", "SMTP_SECURE", "SMTP_TLS_INSECURE"];

beforeEach(() => {
  for (const k of [...FAMILY, ...LEGACY]) vi.stubEnv(k, "");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("mailerConfig", () => {
  it("unset is the local dev door", () => {
    expect(mailerConfig()).toEqual({
      relay: "", replyTo: undefined,
      host: "localhost", port: 1025, from: "Vexa <no-reply@vexa.ai>",
      user: undefined, pass: undefined, secure: false, insecureTls: false,
    });
  });

  it("reads the whole relay from the VEXA_MAIL_SMTP_* family", () => {
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "smtp.example.com");
    vi.stubEnv("VEXA_MAIL_SMTP_PORT", "465");
    vi.stubEnv("VEXA_MAIL_SMTP_FROM", "Acme <signin@acme.example>");
    vi.stubEnv("VEXA_MAIL_SMTP_USER", "relay-user");
    vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD", "relay-pass");
    vi.stubEnv("VEXA_MAIL_SMTP_SECURE", "1");
    vi.stubEnv("VEXA_MAIL_SMTP_TLS_INSECURE", "true");
    expect(mailerConfig()).toEqual({
      relay: "", replyTo: undefined,
      host: "smtp.example.com", port: 465, from: "Acme <signin@acme.example>",
      user: "relay-user", pass: "relay-pass", secure: true, insecureTls: true,
    });
  });

  it("a set relay with no port is the family default, 25", () => {
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "relay.internal");
    expect(mailerConfig().port).toBe(25);
  });

  it("an old SMTP_* name configures nothing", () => {
    for (const k of LEGACY) vi.stubEnv(k, k === "SMTP_PORT" ? "2525" : "old-value");
    expect(mailerConfig()).toEqual({
      relay: "", replyTo: undefined,
      host: "localhost", port: 1025, from: "Vexa <no-reply@vexa.ai>",
      user: undefined, pass: undefined, secure: false, insecureTls: false,
    });
  });
});


describe("a brokered relay password (PRD 0.13.4 S2)", () => {
  it("the terminal does not try an authenticated relay whose password only flows can read", async () => {
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "smtp.example.test");
    vi.stubEnv("VEXA_MAIL_SMTP_USER", "sender@example.test");
    vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD", "");
    vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp");
    expect(mailerConfig().brokered).toBe(true);
    await expect(sendMail({ to: "a@b.example", subject: "s", text: "t" })).rejects.toThrow(/credential broker/);
  });
});


describe("the recipient allowlist (PRD 0.13.4 S2)", () => {
  it("matches exact addresses and domains; unset allows everyone", () => {
    const env = { VEXA_MAIL_RECIPIENT_ALLOW: "dmitry@vexa.ai, @team.example.org" } as unknown as NodeJS.ProcessEnv;
    expect(recipientAllowed("Dmitry <dmitry@vexa.ai>", env)).toBe(true);
    expect(recipientAllowed("a@team.example.org", env)).toBe(true);
    expect(recipientAllowed("marvin@bank.example-customer.org", env)).toBe(false);
    expect(recipientAllowed("dmitry@vexa.ai.evil.org", env)).toBe(false);
    expect(recipientAllowed("anyone@x.org", {} as NodeJS.ProcessEnv)).toBe(true);
  });

  it("a recipient off the list is never handed to the brokered relay: it is diverted (and logged)", async () => {
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "smtp.example.test");
    vi.stubEnv("VEXA_MAIL_SMTP_USER", "sender@example.test");
    vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD", "");
    vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD_REF", "broker:mail/smtp");
    vi.stubEnv("VEXA_MAIL_RECIPIENT_ALLOW", "dmitry@vexa.ai");
    vi.stubEnv("VEXA_MAIL_CAPTURE_SMTP", "127.0.0.1:1");    // nothing listens: the connect fails, nothing is sent
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // the brokered-relay refusal would throw "credential broker"; a diverted send goes to the capture
    // address instead, which here refuses the connection
    await expect(sendMail({ to: "outsider@partner-org.net", subject: "Secret", text: "body" }, 2000))
      .rejects.not.toThrow(/credential broker/);
    const logged = warn.mock.calls.map((c) => String(c[0])).join(" ");
    expect(logged).toContain("mail_diverted to_domain=partner-org.net");
    expect(logged).not.toContain("Secret");
    // an allowed recipient still meets the brokered-relay rule
    await expect(sendMail({ to: "dmitry@vexa.ai", subject: "s", text: "t" })).rejects.toThrow(/credential broker/);
  });
});
