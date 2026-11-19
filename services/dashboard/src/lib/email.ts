import nodemailer from "nodemailer";

interface SendEmailOptions {
  to: string;
  subject: string;
  html: string;
  text?: string;
}

/**
 * Create SMTP transporter from environment variables
 */
function createTransporter() {
  const host = process.env.SMTP_HOST;
  const port = parseInt(process.env.SMTP_PORT || "587");
  const secure = process.env.SMTP_SECURE === "true";
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASS;

  if (!host || !user || !pass) {
    throw new Error("SMTP configuration is incomplete. Please set SMTP_HOST, SMTP_USER, and SMTP_PASS.");
  }

  return nodemailer.createTransport({
    host,
    port,
    secure,
    auth: {
      user,
      pass,
    },
  });
}

/** The bare address out of `Name <a@b>` or `a@b`, lower-cased. */
function bareAddress(addr: string): string {
  const s = addr || "";
  const open = s.indexOf("<");
  const close = open >= 0 ? s.indexOf(">", open + 1) : -1;
  return (close > open + 1 ? s.slice(open + 1, close) : s).trim().toLowerCase();
}

/**
 * `VEXA_MAIL_RECIPIENT_ALLOW` (PRD 0.13.4 S2), the same key flows and the terminal read: a comma list
 * of exact addresses and domains (`@vexa.ai` or `vexa.ai`). Unset: everyone. Set: a recipient not on
 * it is never sent to — the message goes to the capture double instead.
 */
export function recipientAllowed(to: string, env: NodeJS.ProcessEnv = process.env): boolean {
  const allow = (env.VEXA_MAIL_RECIPIENT_ALLOW || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  if (!allow.length) return true;
  const addr = bareAddress(to);
  const domain = addr.split("@")[1] || "";
  return allow.some((e) => (e.includes("@") && !e.startsWith("@") ? addr === e : !!domain && domain === e.replace(/^@/, "")));
}

/** The capture double (`VEXA_MAIL_CAPTURE_SMTP`, default `mailpit:1025`): plain SMTP, no login. */
function createCaptureTransporter() {
  const raw = (process.env.VEXA_MAIL_CAPTURE_SMTP || "mailpit:1025").trim();
  const i = raw.lastIndexOf(":");
  const port = i > 0 ? parseInt(raw.slice(i + 1), 10) : 1025;
  return nodemailer.createTransport({
    host: i > 0 ? raw.slice(0, i) : raw,
    port: Number.isFinite(port) ? port : 1025,
    secure: false,
    ignoreTLS: true,
  });
}

/**
 * Send an email using SMTP — or, for a recipient off `VEXA_MAIL_RECIPIENT_ALLOW`, to the capture
 * double, logged `mail_diverted` with the recipient's domain only (no subject, body or link).
 */
export async function sendEmail({ to, subject, html, text }: SendEmailOptions): Promise<void> {
  let transporter;
  if (recipientAllowed(to)) {
    transporter = createTransporter();
  } else {
    console.warn(`[dashboard-mail] mail_diverted to_domain=${bareAddress(to).split("@")[1] || "?"} — not on VEXA_MAIL_RECIPIENT_ALLOW; sent to the capture double instead`);
    transporter = createCaptureTransporter();
  }
  const from = process.env.SMTP_FROM || process.env.SMTP_USER;

  await transporter.sendMail({
    from,
    to,
    subject,
    html,
    text: text || html.replace(/<[^>]*>/g, ""),
  });
}

/**
 * Send magic link email for authentication
 */
export async function sendMagicLinkEmail(email: string, magicLink: string): Promise<void> {
  const appName = "Vexa";

  const html = `
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sign in to ${appName}</title>
      </head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #fff; margin: 0; padding: 0;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #fff;">
          <tr>
            <td align="center" style="padding: 40px 20px;">
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 480px;">
                <!-- Logo -->
                <tr>
                  <td style="padding-bottom: 32px;">
                    <span style="font-size: 24px; font-weight: 700; color: #000;">${appName}</span>
                  </td>
                </tr>

                <!-- Main content -->
                <tr>
                  <td style="padding-bottom: 24px;">
                    <p style="margin: 0; font-size: 14px; line-height: 24px; color: #000;">
                      Click the button below to sign in to your ${appName} account. This link will expire in 15 minutes.
                    </p>
                  </td>
                </tr>

                <!-- Button -->
                <tr>
                  <td style="padding-bottom: 32px;">
                    <a href="${magicLink}" style="display: inline-block; background-color: #000; color: #fff; font-size: 14px; font-weight: 500; text-decoration: none; padding: 12px 24px; border-radius: 5px;">Sign in</a>
                  </td>
                </tr>

                <!-- Link fallback -->
                <tr>
                  <td style="padding-bottom: 32px;">
                    <p style="margin: 0 0 8px 0; font-size: 14px; line-height: 24px; color: #666;">
                      Or copy and paste this URL into your browser:
                    </p>
                    <p style="margin: 0; font-size: 14px; line-height: 24px; color: #666; word-break: break-all;">
                      ${magicLink}
                    </p>
                  </td>
                </tr>

                <!-- Footer -->
                <tr>
                  <td style="border-top: 1px solid #eaeaea; padding-top: 24px;">
                    <p style="margin: 0; font-size: 12px; line-height: 20px; color: #666;">
                      If you didn't request this email, you can safely ignore it.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
  `;

  const text = `Sign in to ${appName}

Click the link below to sign in to your account. This link will expire in 15 minutes.

${magicLink}

If you didn't request this email, you can safely ignore it.`;

  await sendEmail({
    to: email,
    subject: `Sign in to ${appName}`,
    html,
    text,
  });
}
