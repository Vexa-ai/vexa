import { chromium } from "playwright";

const dashboardUrl = process.env.DASHBOARD_URL;
const gatewayUrl = process.env.GATEWAY_URL;
const apiToken = process.env.API_TOKEN;
const cookieName = process.env.DASHBOARD_COOKIE_NAME;
const meetingId = process.env.MEETING_ID;
const sessionToken = process.env.SESSION_TOKEN;

if (!dashboardUrl || !gatewayUrl || !apiToken || !cookieName || !meetingId) {
  throw new Error("DASHBOARD_URL, GATEWAY_URL, API_TOKEN, DASHBOARD_COOKIE_NAME, and MEETING_ID are required");
}

const dashboardOrigin = new URL(dashboardUrl).origin;
const gatewayOrigin = new URL(gatewayUrl).origin;
const expectedBrowserPrefix = `${gatewayOrigin}/b/`;

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
});

try {
  const context = await browser.newContext({
    baseURL: dashboardUrl,
    viewport: { width: 1280, height: 800 },
  });
  await context.addCookies([
    {
      name: cookieName,
      value: apiToken,
      domain: new URL(dashboardUrl).hostname,
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);

  const page = await context.newPage();
  const requestFailures = [];
  const serverFailures = [];
  const vncResponses = [];

  page.on("requestfailed", (request) => {
    requestFailures.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || "failed"}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      serverFailures.push(`${response.status()} ${response.url()}`);
    }
    if (response.url().includes("/b/") && response.url().includes("/vnc/vnc.html")) {
      vncResponses.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto(`/meetings/${meetingId}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForTimeout(1_500);

  const browserButton = page.getByRole("button", { name: /^Browser$/ }).first();
  if (!(await browserButton.isVisible().catch(() => false))) {
    const text = await page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
    throw new Error(`Browser button not visible on meeting ${meetingId}: ${text.replace(/\s+/g, " ").slice(0, 400)}`);
  }

  await browserButton.click();
  await page.waitForSelector("iframe", { timeout: 10_000 });
  await page.waitForTimeout(2_500);

  const iframeSrc = await page.locator("iframe").first().getAttribute("src");
  if (!iframeSrc) {
    throw new Error("Browser view iframe did not render a src");
  }
  if (!iframeSrc.startsWith(expectedBrowserPrefix)) {
    throw new Error(`iframe src does not use gateway origin: got=${iframeSrc} expectedPrefix=${expectedBrowserPrefix}`);
  }
  if (iframeSrc.startsWith(`${dashboardOrigin}/b/`)) {
    throw new Error(`iframe src still uses dashboard same-origin /b route: ${iframeSrc}`);
  }
  if (sessionToken && !iframeSrc.includes(`/b/${sessionToken}/`)) {
    throw new Error(`iframe src did not prefer session token ${sessionToken}: ${iframeSrc}`);
  }

  const blocked = requestFailures.filter((line) => line.includes("ERR_BLOCKED_BY_RESPONSE") || line.includes("/b/"));
  if (blocked.length > 0) {
    throw new Error(`browser view request failures: ${blocked.slice(0, 5).join(" | ")}`);
  }
  if (serverFailures.length > 0) {
    throw new Error(`server failures while loading browser view: ${serverFailures.slice(0, 5).join(" | ")}`);
  }
  if (vncResponses.length === 0) {
    throw new Error(`no VNC iframe response observed for ${iframeSrc}`);
  }
  if (!vncResponses.some((line) => line.startsWith("200 "))) {
    throw new Error(`VNC iframe did not return HTTP 200: ${vncResponses.join(" | ")}`);
  }

  console.log(`PASS iframe=${iframeSrc} vnc=${vncResponses[0]}`);
} finally {
  await browser.close();
}
