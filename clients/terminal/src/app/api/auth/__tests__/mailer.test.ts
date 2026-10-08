/** The sign-in mail's settings are the deployment's mail family, VEXA_MAIL_SMTP_* (S2).
 *
 *  The terminal used to read seven unprefixed SMTP_* keys that no contract declared, next to the
 *  VEXA_MAIL_SMTP_* family flows already sends through. It now reads the family (declared in
 *  clients/terminal/config.v1.json, held on compose, Helm and Lite by gate:config-contract) and
 *  honours the old names only as a fallback that says so.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetMailWarnings, mailerConfig } from "../mailer";

const FAMILY = ["HOST", "PORT", "FROM", "USER", "PASSWORD", "SECURE", "TLS_INSECURE"].map((k) => `VEXA_MAIL_SMTP_${k}`);
const LEGACY = ["SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USER", "SMTP_PASS", "SMTP_SECURE", "SMTP_TLS_INSECURE"];

beforeEach(() => {
  for (const k of [...FAMILY, ...LEGACY]) vi.stubEnv(k, "");
  _resetMailWarnings();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("mailerConfig", () => {
  it("unset is the local dev door", () => {
    expect(mailerConfig()).toEqual({
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
      host: "smtp.example.com", port: 465, from: "Acme <signin@acme.example>",
      user: "relay-user", pass: "relay-pass", secure: true, insecureTls: true,
    });
  });

  it("a set relay with no port is the family default, 25", () => {
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "relay.internal");
    expect(mailerConfig().port).toBe(25);
  });

  it("honours a pre-v0.13.2 SMTP_* name, and says which key to set instead", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("SMTP_HOST", "old-relay.example");
    vi.stubEnv("SMTP_PASS", "old-pass");
    const cfg = mailerConfig();
    expect(cfg.host).toBe("old-relay.example");
    expect(cfg.pass).toBe("old-pass");
    expect(warn.mock.calls.map((c) => String(c[0])).join("\n")).toContain("set VEXA_MAIL_SMTP_HOST instead");
  });

  it("the family wins over an old name set beside it", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("SMTP_HOST", "old-relay.example");
    vi.stubEnv("VEXA_MAIL_SMTP_HOST", "new-relay.example");
    expect(mailerConfig().host).toBe("new-relay.example");
  });
});
