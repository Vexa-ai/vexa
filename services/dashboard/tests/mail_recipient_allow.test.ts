/** VEXA_MAIL_RECIPIENT_ALLOW in the legacy dashboard mailer (PRD 0.13.4 S2): no mail path sits outside
 *  the guard. An allowed recipient goes to the configured SMTP relay; anybody else goes to the capture
 *  double, and the log says `mail_diverted` with the domain only. nodemailer is a recording double. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const made: { opts: Record<string, unknown>; sent: Record<string, unknown>[] }[] = [];
vi.mock("nodemailer", () => ({
  default: {
    createTransport: (opts: Record<string, unknown>) => {
      const t = { opts, sent: [] as Record<string, unknown>[] };
      made.push(t);
      return { sendMail: async (m: Record<string, unknown>) => { t.sent.push(m); } };
    },
  },
}));

import { recipientAllowed, sendEmail, sendMagicLinkEmail } from "../src/lib/email";

beforeEach(() => {
  made.length = 0;
  vi.stubEnv("SMTP_HOST", "smtp.relay.test-provider.net");
  vi.stubEnv("SMTP_USER", "sender@vexa.ai");
  vi.stubEnv("SMTP_PASS", "relay-pass");
  vi.stubEnv("VEXA_MAIL_CAPTURE_SMTP", "mailpit:1025");
  vi.stubEnv("VEXA_MAIL_RECIPIENT_ALLOW", "dmitry@vexa.ai");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("the dashboard mailer honours VEXA_MAIL_RECIPIENT_ALLOW", () => {
  it("sends an allowed recipient through the relay", async () => {
    await sendEmail({ to: "dmitry@vexa.ai", subject: "s", html: "<p>b</p>" });
    expect(made).toHaveLength(1);
    expect(made[0].opts.host).toBe("smtp.relay.test-provider.net");
    expect(made[0].sent[0].to).toBe("dmitry@vexa.ai");
  });

  it("diverts anybody else to the capture double and logs no content", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await sendMagicLinkEmail("marvin@bank.example-customer.org", "https://app.example.test/magic?t=SECRET");
    expect(made).toHaveLength(1);
    expect(made[0].opts).toMatchObject({ host: "mailpit", port: 1025, secure: false });
    expect(made[0].opts.auth).toBeUndefined();
    const logged = warn.mock.calls.map((c) => String(c[0])).join(" ");
    expect(logged).toContain("mail_diverted to_domain=bank.example-customer.org");
    expect(logged).not.toContain("SECRET");
    expect(logged).not.toContain("marvin@");
  });

  it("unset means no restriction", async () => {
    vi.stubEnv("VEXA_MAIL_RECIPIENT_ALLOW", "");
    await sendEmail({ to: "someone@partner-org.net", subject: "s", html: "b" });
    expect(made[0].opts.host).toBe("smtp.relay.test-provider.net");
  });

  it("matches exact addresses and domains", () => {
    const env = { VEXA_MAIL_RECIPIENT_ALLOW: "dmitry@vexa.ai,@team.example.org" } as unknown as NodeJS.ProcessEnv;
    expect(recipientAllowed("Dmitry <DMITRY@vexa.ai>", env)).toBe(true);
    expect(recipientAllowed("a@team.example.org", env)).toBe(true);
    expect(recipientAllowed("dmitry@vexa.ai.evil.org", env)).toBe(false);
    expect(recipientAllowed("x@sub.team.example.org", env)).toBe(false);
  });
});
