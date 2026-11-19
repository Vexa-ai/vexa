/** What the sign-in mailer says on the wire, against a scripted relay on loopback.
 *
 *  The property under test: SMTP credentials and the message never cross an unencrypted connection
 *  when a user and password are configured. A relay that offers STARTTLS is upgraded before AUTH; a
 *  relay that offers neither implicit TLS nor STARTTLS is refused before AUTH, and nothing is sent.
 *  Without credentials (the dev double) plain TCP still delivers.
 *
 *  The relay's certificate is made for the run with `openssl` (self-signed, so the cases that use it
 *  set VEXA_MAIL_SMTP_TLS_INSECURE); no key material is kept in the repository.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import tls from "node:tls";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { sendMail } from "../mailer";

type Seen = { line: string; encrypted: boolean };

let certDir = "";
let key = "";
let cert = "";

beforeAll(() => {
  certDir = mkdtempSync(join(tmpdir(), "vexa-smtp-test-"));
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
    "-keyout", join(certDir, "k.pem"), "-out", join(certDir, "c.pem")], { stdio: "ignore" });
  key = readFileSync(join(certDir, "k.pem"), "utf8");
  cert = readFileSync(join(certDir, "c.pem"), "utf8");
});
afterAll(() => rmSync(certDir, { recursive: true, force: true }));
afterEach(() => vi.unstubAllEnvs());

/** A relay that records every line it receives and whether it arrived encrypted. */
async function relay(offerStartTls: boolean): Promise<{ port: number; seen: Seen[]; close: () => Promise<void> }> {
  const seen: Seen[] = [];
  const server = net.createServer((conn) => {
    let sock: net.Socket = conn;
    let encrypted = false;
    let inData = false;
    let buf = "";
    const send = (s: string) => sock.write(s);
    const onLine = (line: string) => {
      seen.push({ line, encrypted });
      if (inData) {
        if (line === ".") { inData = false; send("250 queued\r\n"); }
        return;
      }
      const verb = line.split(" ")[0].toUpperCase();
      if (verb === "EHLO") send(`250-relay\r\n${offerStartTls && !encrypted ? "250-STARTTLS\r\n" : ""}250 AUTH LOGIN\r\n`);
      else if (verb === "STARTTLS") {
        send("220 go ahead\r\n");
        sock.removeAllListeners("data");
        const secured = new tls.TLSSocket(sock, { isServer: true, key, cert });
        secured.setEncoding("utf8");
        sock = secured;
        encrypted = true;
        secured.on("data", onData);
      } else if (verb === "AUTH") send("334 VXNlcm5hbWU6\r\n");
      else if (seen.length >= 2 && seen[seen.length - 2].line.startsWith("AUTH")) send("334 UGFzc3dvcmQ6\r\n");
      else if (seen.length >= 3 && seen[seen.length - 3].line.startsWith("AUTH")) send("235 ok\r\n");
      else if (verb === "MAIL" || verb === "RCPT") send("250 ok\r\n");
      else if (verb === "DATA") { inData = true; send("354 go\r\n"); }
      else if (verb === "QUIT") { send("221 bye\r\n"); sock.end(); }
      else send("500 what\r\n");
    };
    const onData = (chunk: string) => {
      buf += chunk;
      let i: number;
      while ((i = buf.indexOf("\r\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 2); onLine(line); }
    };
    conn.setEncoding("utf8");
    conn.on("data", onData);
    conn.on("error", () => {});
    send("220 relay ready\r\n");
  });
  await new Promise<void>((res) => server.listen(0, "127.0.0.1", () => res()));
  const port = (server.address() as net.AddressInfo).port;
  return { port, seen, close: () => new Promise<void>((res) => server.close(() => res())) };
}

function useRelay(port: number, withCredentials: boolean) {
  vi.stubEnv("VEXA_MAIL_SMTP_HOST", "localhost");
  vi.stubEnv("VEXA_MAIL_SMTP_PORT", String(port));
  vi.stubEnv("VEXA_MAIL_SMTP_FROM", "Vexa <no-reply@vexa.test>");
  vi.stubEnv("VEXA_MAIL_SMTP_SECURE", "");
  vi.stubEnv("VEXA_MAIL_SMTP_TLS_INSECURE", "1");
  vi.stubEnv("VEXA_MAIL_SMTP_USER", withCredentials ? "relay-user" : "");
  vi.stubEnv("VEXA_MAIL_SMTP_PASSWORD", withCredentials ? "relay-pass" : "");
}

const MAIL = { to: "person@example.test", subject: "Your sign-in link", text: "https://terminal.test/api/auth/redeem?t=x" };

describe("the sign-in mailer on the wire", () => {
  it("with credentials, upgrades a relay that offers STARTTLS before it authenticates or sends", async () => {
    const r = await relay(true);
    try {
      useRelay(r.port, true);
      await sendMail(MAIL, 5000);
      const auth = r.seen.find((s) => s.line.startsWith("AUTH"));
      const mail = r.seen.find((s) => s.line.startsWith("MAIL FROM"));
      expect(r.seen.some((s) => s.line === "STARTTLS")).toBe(true);
      expect(auth?.encrypted).toBe(true);
      expect(mail?.encrypted).toBe(true);
      expect(r.seen.filter((s) => !s.encrypted).map((s) => s.line.split(" ")[0])).toEqual(["EHLO", "STARTTLS"]);
    } finally { await r.close(); }
  });

  it("with credentials, refuses a relay that offers no encryption, before AUTH and before the message", async () => {
    const r = await relay(false);
    try {
      useRelay(r.port, true);
      await expect(sendMail(MAIL, 5000)).rejects.toThrow(/unencrypted/);
      expect(r.seen.some((s) => s.line.startsWith("AUTH"))).toBe(false);
      expect(r.seen.some((s) => s.line.startsWith("MAIL FROM"))).toBe(false);
    } finally { await r.close(); }
  });

  it("without credentials, a plain relay still delivers (the dev mail double)", async () => {
    const r = await relay(false);
    try {
      useRelay(r.port, false);
      await sendMail(MAIL, 5000);
      expect(r.seen.some((s) => s.line.startsWith("MAIL FROM"))).toBe(true);
      expect(r.seen.some((s) => s.line.startsWith("AUTH"))).toBe(false);
    } finally { await r.close(); }
  });

  it("without credentials, a relay that offers STARTTLS gets the message encrypted", async () => {
    const r = await relay(true);
    try {
      useRelay(r.port, false);
      await sendMail(MAIL, 5000);
      expect(r.seen.find((s) => s.line.startsWith("MAIL FROM"))?.encrypted).toBe(true);
    } finally { await r.close(); }
  });
});
