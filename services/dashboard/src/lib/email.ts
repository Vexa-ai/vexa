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

/**
 * Send an email using SMTP
 */
export async function sendEmail({ to, subject, html, text }: SendEmailOptions): Promise<void> {
  const transporter = createTransporter();
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
