/** The smallest SMTP client that can post one plain-text message — the terminal's mail door.
 *
 *  Dependency-free ON PURPOSE. The image is built with `npm ci` from a committed lockfile; adding
 *  nodemailer for ~100 lines of protocol would put a new package (and lockfile churn) on the
 *  release build's critical path for one email. This speaks the subset of RFC 5321 a sign-in mail
 *  needs: EHLO → optional AUTH LOGIN → MAIL FROM → RCPT TO → DATA → QUIT.
 *
 *  Transport shapes, mirroring `flows_steps/emailx.py` (the pattern already in this stack):
 *    • plain TCP (default)  — the dev mail double (Mailpit) on :1025: no auth, no TLS.
 *    • implicit TLS         — SMTP_SECURE=1, e.g. a provider's :465 endpoint, with AUTH LOGIN.
 *  STARTTLS (opportunistic upgrade on :587) is NOT implemented; a provider that requires it needs
 *  its :465 endpoint or a real mailer library.
 *
 *  Env (all optional — the defaults ARE the local dev door):
 *    SMTP_HOST=localhost   SMTP_PORT=1025   SMTP_FROM="Vexa <no-reply@vexa.ai>"
 *    SMTP_USER / SMTP_PASS   (AUTH LOGIN, only when both are present)
 *    SMTP_SECURE=1           (implicit TLS)
 *    SMTP_TLS_INSECURE=1     (skip certificate verification — dev only)
 */
import net from "node:net";
import tls from "node:tls";

export interface MailerConfig {
  host: string;
  port: number;
  from: string;
  user?: string;
  pass?: string;
  secure: boolean;
  insecureTls: boolean;
}

export function mailerConfig(): MailerConfig {
  const port = parseInt(process.env.SMTP_PORT || "", 10);
  const truthy = (v: string | undefined) => v === "1" || v === "true";
  return {
    host: process.env.SMTP_HOST || "localhost",
    port: Number.isFinite(port) && port > 0 ? port : 1025,
    from: process.env.SMTP_FROM || "Vexa <no-reply@vexa.ai>",
    user: process.env.SMTP_USER || undefined,
    pass: process.env.SMTP_PASS || undefined,
    secure: truthy(process.env.SMTP_SECURE),
    insecureTls: truthy(process.env.SMTP_TLS_INSECURE),
  };
}

type Reply = { code: number; text: string };

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
function buildMessage(from: string, to: string, subject: string, text: string, domain: string): string {
  const messageId = `<${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}@${domain}>`;
  const headers = [
    `From: ${from}`,
    `To: ${to}`,
    `Subject: ${subject}`,
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
export async function sendMail(opts: { to: string; subject: string; text: string }, timeoutMs = 15000): Promise<void> {
  const cfg = mailerConfig();
  const wire = new Wire();

  const socket: net.Socket = cfg.secure
    ? tls.connect({ host: cfg.host, port: cfg.port, servername: cfg.host, rejectUnauthorized: !cfg.insecureTls })
    : net.connect({ host: cfg.host, port: cfg.port });
  socket.setEncoding("utf8");
  socket.setTimeout(timeoutMs);
  socket.on("data", (d: string | Buffer) => wire.push(typeof d === "string" ? d : d.toString("utf8")));
  socket.on("error", (e) => wire.fail(e as Error));
  socket.on("timeout", () => {
    wire.fail(new Error(`SMTP timeout after ${timeoutMs}ms (${cfg.host}:${cfg.port})`));
    socket.destroy();
  });
  socket.on("close", () => wire.fail(new Error(`SMTP connection closed (${cfg.host}:${cfg.port})`)));

  const expect = async (ok: number[], step: string): Promise<Reply> => {
    const r = await wire.next();
    if (!ok.includes(r.code)) throw new Error(`SMTP ${step}: ${r.code} ${r.text}`);
    return r;
  };
  const say = (line: string) =>
    new Promise<void>((res, rej) => socket.write(`${line}\r\n`, (e) => (e ? rej(e) : res())));

  try {
    await new Promise<void>((res, rej) => {
      const onError = (e: Error) => rej(e);
      socket.once("error", onError);
      socket.once(cfg.secure ? "secureConnect" : "connect", () => {
        socket.off("error", onError);
        res();
      });
    });

    await expect([220], "greeting");
    const heloDomain = bareAddress(cfg.from).split("@")[1] || "localhost";
    await say(`EHLO ${heloDomain}`);
    await expect([250], "EHLO");

    if (cfg.user && cfg.pass) {
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
    await say(buildMessage(cfg.from, opts.to, opts.subject, opts.text, heloDomain));
    await say(".");
    await expect([250], "message body");
    await say("QUIT");
  } finally {
    // A clean QUIT closes the socket; that must not surface as a "connection closed" failure.
    socket.removeAllListeners("close");
    socket.destroy();
  }
}
