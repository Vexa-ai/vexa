/** The smallest SMTP client that can post one plain-text message — the terminal's mail door.
 *
 *  Dependency-free ON PURPOSE. The image is built with `npm ci` from a committed lockfile; adding
 *  nodemailer for ~100 lines of protocol would put a new package (and lockfile churn) on the
 *  release build's critical path for one email. This speaks the subset of RFC 5321 (and RFC 3207)
 *  a sign-in mail needs: EHLO → STARTTLS when offered → optional AUTH LOGIN → MAIL FROM → RCPT TO →
 *  DATA → QUIT.
 *
 *  Transport shapes, mirroring `flows_steps/emailx.py` (the pattern already in this stack):
 *    • implicit TLS — VEXA_MAIL_SMTP_SECURE=1, e.g. a provider's :465 endpoint.
 *    • STARTTLS     — a relay that offers it after EHLO (a provider's :587) is upgraded before anything
 *                     else is said, with the certificate verified, and greeted again.
 *    • plain TCP    — a relay that offers neither, e.g. the dev mail double (Mailpit) on :1025.
 *  CREDENTIALS NEVER CROSS AN UNENCRYPTED CONNECTION: with a user and password set, a relay that ends
 *  up in plain TCP is refused before AUTH, and nothing is sent.
 *
 *  Env — the deployment's mail family, VEXA_MAIL_SMTP_*, which flows sends through too and which is
 *  declared in ../../../../config.v1.json (held on compose, Helm and Lite by gate:config-contract):
 *    VEXA_MAIL_SMTP_HOST      unset = the local dev door, localhost:1025
 *    VEXA_MAIL_SMTP_PORT      default 25 for a set host (1025 for the dev door)
 *    VEXA_MAIL_SMTP_FROM      default "Vexa <no-reply@vexa.ai>"
 *    VEXA_MAIL_SMTP_USER / VEXA_MAIL_SMTP_PASSWORD   AUTH LOGIN, only when both are present, and
 *                                   only over implicit TLS or STARTTLS
 *    VEXA_MAIL_SMTP_SECURE=1        implicit TLS
 *    VEXA_MAIL_SMTP_TLS_INSECURE=1  skip certificate verification (implicit TLS and STARTTLS) — dev only
 *    VEXA_MAIL_REPLY_TO             Reply-To on every mail (the mailbox the inbound sensor reads)
 *    VEXA_MAIL_RELAY                THE ONE SWITCH, shared with flows: unset = as above; `capture` = every
 *                                   mail goes to VEXA_MAIL_CAPTURE_SMTP (`mailpit:1025`), no login, nothing
 *                                   reaches a real mailbox; `relay` = the VEXA_MAIL_SMTP_* relay for real,
 *                                   and then a recipient at a test domain is never relayed.
 *    VEXA_MAIL_SMTP_PASSWORD_REF    `broker:mail/<name>` (PRD 0.13.4 S2): the relay's password lives in
 *                                   the credential broker, which only flows reads. With a ref and no
 *                                   raw password, THIS mailer does not send to an authenticated relay —
 *                                   flows is the one sender (`linkDelivery.ts` asks it first) — and
 *                                   says so instead of trying an AUTH-less login.
 *    VEXA_MAIL_RECIPIENT_ALLOW      shared with flows: when set, a recipient not on it is never sent to —
 *                                   the message goes to VEXA_MAIL_CAPTURE_SMTP and `mail_diverted` is logged.
 *  These keys are the only source: the defaults above are the declared ones.
 */
import net from "node:net";
import tls from "node:tls";

export interface MailerConfig {
  relay: "" | "capture" | "relay";
  /** The relay password is held by the credential broker (a ref), not by this process. */
  brokered?: boolean;
  replyTo?: string;
  host: string;
  port: number;
  from: string;
  user?: string;
  pass?: string;
  secure: boolean;
  insecureTls: boolean;
}

export function mailerConfig(): MailerConfig {
  const truthy = (v: string | undefined) => v === "1" || v === "true";
  const relayRaw = (process.env.VEXA_MAIL_RELAY || "").trim().toLowerCase();
  const relay: MailerConfig["relay"] = relayRaw === "capture" || relayRaw === "relay" ? relayRaw : "";
  const replyTo = oneLine(process.env.VEXA_MAIL_REPLY_TO || "") || undefined;
  if (relay === "capture") {
    const raw = (process.env.VEXA_MAIL_CAPTURE_SMTP || "mailpit:1025").trim();
    const i = raw.lastIndexOf(":");
    const cport = i > 0 ? parseInt(raw.slice(i + 1), 10) : 1025;
    return {
      relay, replyTo, host: i > 0 ? raw.slice(0, i) : raw, port: Number.isFinite(cport) ? cport : 1025,
      from: process.env.VEXA_MAIL_SMTP_FROM || "Vexa <no-reply@vexa.ai>",
      secure: false, insecureTls: false,
    };
  }
  const host = process.env.VEXA_MAIL_SMTP_HOST || undefined;
  const port = parseInt(process.env.VEXA_MAIL_SMTP_PORT || "", 10);
  return {
    relay, replyTo,
    host: host || "localhost",
    port: Number.isFinite(port) && port > 0 ? port : host ? 25 : 1025,
    from: process.env.VEXA_MAIL_SMTP_FROM || "Vexa <no-reply@vexa.ai>",
    user: process.env.VEXA_MAIL_SMTP_USER || undefined,
    pass: process.env.VEXA_MAIL_SMTP_PASSWORD || undefined,
    ...(!process.env.VEXA_MAIL_SMTP_PASSWORD && (process.env.VEXA_MAIL_SMTP_PASSWORD_REF || "").trim()
      ? { brokered: true } : {}),
    secure: truthy(process.env.VEXA_MAIL_SMTP_SECURE),
    insecureTls: truthy(process.env.VEXA_MAIL_SMTP_TLS_INSECURE),
  };
}

type Reply = { code: number; text: string };

/** One header value with no line structure left in it. */
function oneLine(v: string): string {
  return v.replace(/[\r\n]+/g, " ").trim();
}

/** Addresses at these domains are never handed to a real relay (`VEXA_MAIL_RELAY=relay`) — the same
 *  rule as flows' `emailx.is_test_domain`. */
export function isTestDomain(address: string): boolean {
  const d = (bareAddress(address).split("@")[1] || "").toLowerCase();
  return [".test", ".invalid", ".localhost", ".example"].some((t) => d.endsWith(t)) ||
    ["example.com", "example.org", "example.net", "localhost", "test", "invalid", "example"].includes(d);
}

/** Line-buffers the socket into complete SMTP replies, and turns a dead socket into a REJECTED
 *  read rather than a hung one (a request handler waiting forever on a closed connection is the
 *  failure mode that makes hand-rolled protocol clients dangerous). A reply is zero or more
 *  "NNN-continuation" lines terminated by one "NNN final" line (space, not hyphen, after the code). */
class Wire {
  private buf = "";
  private lines: string[] = [];
  private queue: Reply[] = [];
  private waiters: Array<{ res: (r: Reply) => void; rej: (e: Error) => void }> = [];
  private error: Error | null = null;

  push(chunk: string): void {
    this.buf += chunk;
    let i: number;
    while ((i = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, i).replace(/\r$/, "");
      this.buf = this.buf.slice(i + 1);
      this.lines.push(line);
      if (/^\d{3}(?: |$)/.test(line)) {
        const reply: Reply = { code: parseInt(line.slice(0, 3), 10), text: this.lines.join(" | ") };
        this.lines = [];
        const w = this.waiters.shift();
        if (w) w.res(reply);
        else this.queue.push(reply);
      }
    }
  }

  fail(err: Error): void {
    if (this.error) return;
    this.error = err;
    for (const w of this.waiters.splice(0)) w.rej(err);
  }

  next(): Promise<Reply> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    if (this.error) return Promise.reject(this.error);
    return new Promise<Reply>((res, rej) => this.waiters.push({ res, rej }));
  }
}

/** RFC 5322 message, WITHOUT a trailing CRLF (the caller's line-writer supplies it, then "."). */
function buildMessage(from: string, to: string, subject: string, text: string, domain: string,
                      replyTo?: string): string {
  const messageId = `<${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}@${domain}>`;
  const headers = [
    `From: ${from}`,
    `To: ${to}`,
    ...(replyTo ? [`Reply-To: ${replyTo}`] : []),
    `Subject: ${oneLine(subject)}`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: ${messageId}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="utf-8"',
    "Content-Transfer-Encoding: 8bit",
  ].join("\r\n");
  // Dot-stuffing: a body line of "." alone would otherwise terminate DATA.
  const body = text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => (l.startsWith(".") ? `.${l}` : l))
    .join("\r\n");
  return `${headers}\r\n\r\n${body}`;
}

/** The bare address inside "Name <addr>" — what MAIL FROM / RCPT TO take. */
function bareAddress(addr: string): string {
  const m = /<([^>]+)>/.exec(addr);
  return (m ? m[1] : addr).trim();
}

/** Send one plain-text message. THROWS on any protocol or connection failure — the caller decides
 *  whether that is fatal (for /api/auth/request-link it is not, and is never leaked to the client). */
/** `VEXA_MAIL_RECIPIENT_ALLOW` (PRD 0.13.4 S2), shared with flows: a comma list of exact addresses and
 *  domains (`@vexa.ai` or `vexa.ai`). Unset: everyone. */
export function recipientAllowed(to: string, env: NodeJS.ProcessEnv = process.env): boolean {
  const allow = (env.VEXA_MAIL_RECIPIENT_ALLOW || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  if (!allow.length) return true;
  const addr = bareAddress(to).toLowerCase();
  const domain = addr.split("@")[1] || "";
  return allow.some((e) => (e.includes("@") && !e.startsWith("@") ? addr === e : !!domain && domain === e.replace(/^@/, "")));
}

/** The capture double a diverted message goes to (`VEXA_MAIL_CAPTURE_SMTP`, default `mailpit:1025`). */
function captureConfig(base: MailerConfig): MailerConfig {
  const raw = (process.env.VEXA_MAIL_CAPTURE_SMTP || "mailpit:1025").trim();
  const i = raw.lastIndexOf(":");
  const port = i > 0 ? parseInt(raw.slice(i + 1), 10) : 1025;
  return { relay: "capture", replyTo: base.replyTo, host: i > 0 ? raw.slice(0, i) : raw,
    port: Number.isFinite(port) ? port : 1025, from: base.from, secure: false, insecureTls: false };
}

export async function sendMail(opts: { to: string; subject: string; text: string }, timeoutMs = 15000): Promise<void> {
  let cfg = mailerConfig();
  // NOT ON THE ALLOWLIST (PRD 0.13.4 S2): never sent — diverted to the capture double, and logged
  // without content (the recipient's domain only).
  if (!recipientAllowed(opts.to)) {
    console.warn(`[terminal-mail] mail_diverted to_domain=${bareAddress(opts.to).split("@")[1] || "?"} — not on VEXA_MAIL_RECIPIENT_ALLOW; sent to the capture double instead`);
    cfg = captureConfig(cfg);
  }
  if (cfg.brokered && cfg.user) {
    throw new Error("the relay's password is held by the credential broker (VEXA_MAIL_SMTP_PASSWORD_REF); "
      + "flows sends this deployment's mail — the terminal's own mailer does not");
  }
  if (cfg.relay === "relay" && isTestDomain(opts.to)) {
    throw new Error(`refusing to relay to a test domain (${bareAddress(opts.to).split("@")[1]})`);
  }
  let wire = new Wire();
  let encrypted = cfg.secure;

  let socket: net.Socket = cfg.secure
    ? tls.connect({ host: cfg.host, port: cfg.port, servername: cfg.host, rejectUnauthorized: !cfg.insecureTls })
    : net.connect({ host: cfg.host, port: cfg.port });
  const attach = (s: net.Socket, w: Wire) => {
    s.setEncoding("utf8");
    s.setTimeout(timeoutMs);
    s.on("data", (d: string | Buffer) => w.push(typeof d === "string" ? d : d.toString("utf8")));
    s.on("error", (e) => w.fail(e as Error));
    s.on("timeout", () => {
      w.fail(new Error(`SMTP timeout after ${timeoutMs}ms (${cfg.host}:${cfg.port})`));
      s.destroy();
    });
    s.on("close", () => w.fail(new Error(`SMTP connection closed (${cfg.host}:${cfg.port})`)));
  };
  attach(socket, wire);
  const ready = (s: net.Socket, event: "connect" | "secureConnect") =>
    new Promise<void>((res, rej) => {
      const onError = (e: Error) => rej(e);
      s.once("error", onError);
      s.once(event, () => {
        s.off("error", onError);
        res();
      });
    });

  const expect = async (ok: number[], step: string): Promise<Reply> => {
    const r = await wire.next();
    if (!ok.includes(r.code)) throw new Error(`SMTP ${step}: ${r.code} ${r.text}`);
    return r;
  };
  const say = (line: string) =>
    new Promise<void>((res, rej) => socket.write(`${line}\r\n`, (e) => (e ? rej(e) : res())));

  try {
    await ready(socket, cfg.secure ? "secureConnect" : "connect");

    await expect([220], "greeting");
    const heloDomain = bareAddress(cfg.from).split("@")[1] || "localhost";
    await say(`EHLO ${heloDomain}`);
    const ehlo = await expect([250], "EHLO");

    // STARTTLS (RFC 3207): the relay offered it, so the rest of the session is encrypted, with the
    // relay's certificate verified exactly as for implicit TLS. The relay forgets what was said before
    // the upgrade, so it is greeted again.
    if (!encrypted && /(^|\| )250[- ]STARTTLS\b/i.test(ehlo.text)) {
      await say("STARTTLS");
      await expect([220], "STARTTLS");
      const plain = socket;
      for (const event of ["data", "error", "timeout", "close"]) plain.removeAllListeners(event);
      plain.setTimeout(0);
      socket = tls.connect({ socket: plain, servername: cfg.host, rejectUnauthorized: !cfg.insecureTls });
      wire = new Wire();
      attach(socket, wire);
      await ready(socket, "secureConnect");
      encrypted = true;
      await say(`EHLO ${heloDomain}`);
      await expect([250], "EHLO after STARTTLS");
    }

    if (cfg.relay !== "capture" && cfg.user && cfg.pass) {
      if (!encrypted) {
        throw new Error(`SMTP: refusing to send credentials to ${cfg.host}:${cfg.port} over an unencrypted ` +
          "connection — the relay offers no STARTTLS (set VEXA_MAIL_SMTP_SECURE=1 for an implicit-TLS endpoint)");
      }
      await say("AUTH LOGIN");
      await expect([334], "AUTH LOGIN");
      await say(Buffer.from(cfg.user, "utf8").toString("base64"));
      await expect([334], "AUTH username");
      await say(Buffer.from(cfg.pass, "utf8").toString("base64"));
      await expect([235], "AUTH password");
    }

    const from = bareAddress(cfg.from);
    await say(`MAIL FROM:<${from}>`);
    await expect([250], "MAIL FROM");
    await say(`RCPT TO:<${bareAddress(opts.to)}>`);
    await expect([250, 251], "RCPT TO");
    await say("DATA");
    await expect([354], "DATA");
    await say(buildMessage(cfg.from, opts.to, opts.subject, opts.text, heloDomain, cfg.replyTo));
    await say(".");
    await expect([250], "message body");
    await say("QUIT");
  } finally {
    // A clean QUIT closes the socket; that must not surface as a "connection closed" failure.
    socket.removeAllListeners("close");
    socket.destroy();
  }
}
