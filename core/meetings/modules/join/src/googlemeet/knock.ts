import { Page } from "playwright";
import { logJSON } from "../_host";

const MEETING_CODE_PATTERN = /\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b/gi;
const REDACTED_MEETING_CODE = "<code>";
const MAX_HEADLINE_CHARS = 200;
const MAX_CLIENT_ERROR_PATHS = 10;

interface GoogleKnockDiagnostics {
  armed: boolean;
  waitingTextSeen: boolean;
  disconnectedError: boolean;
  clientErrorPaths: string[];
  listening: boolean;
}

const diagnosticsByPage = new WeakMap<Page, GoogleKnockDiagnostics>();

function diagnosticsFor(page: Page): GoogleKnockDiagnostics {
  let diagnostics = diagnosticsByPage.get(page);
  if (!diagnostics) {
    diagnostics = {
      armed: false,
      waitingTextSeen: false,
      disconnectedError: false,
      clientErrorPaths: [],
      listening: false,
    };
    diagnosticsByPage.set(page, diagnostics);
  }
  return diagnostics;
}

function sanitizeMeetingCode(text: string): string {
  return String(text ?? "").replace(MEETING_CODE_PATTERN, REDACTED_MEETING_CODE);
}

function responsePath(rawUrl: string): string {
  try {
    return new URL(rawUrl).pathname;
  } catch {
    return String(rawUrl ?? "").split(/[?#]/)[0];
  }
}

function installListeners(page: Page, diagnostics: GoogleKnockDiagnostics): void {
  if (diagnostics.listening || typeof (page as any).on !== "function") return;
  diagnostics.listening = true;

  (page as any).on("console", (message: any) => {
    if (!diagnostics.armed) return;
    let text = "";
    try {
      text = String(message?.text?.() ?? message?.text ?? "");
    } catch {
      return;
    }
    if (text.includes("DisconnectedError") && /EndCause\s*=/.test(text)) {
      diagnostics.disconnectedError = true;
    }
  });

  (page as any).on("pageerror", (error: any) => {
    if (!diagnostics.armed) return;
    const text = String(error?.message ?? error ?? "");
    if (text.includes("DisconnectedError") && /EndCause\s*=/.test(text)) {
      diagnostics.disconnectedError = true;
    }
  });

  (page as any).on("response", (response: any) => {
    if (!diagnostics.armed) return;
    let status = 0;
    try {
      status = Number(response?.status?.() ?? 0);
    } catch {
      return;
    }
    if (status < 400 || status >= 500) return;

    let rawUrl = "";
    try {
      rawUrl = String(response?.url?.() ?? "");
    } catch {
      return;
    }
    const path = sanitizeMeetingCode(responsePath(rawUrl));
    if (!path || diagnostics.clientErrorPaths.includes(path)) return;
    if (diagnostics.clientErrorPaths.length < MAX_CLIENT_ERROR_PATHS) {
      diagnostics.clientErrorPaths.push(path);
    }
  });
}

/** Arm the per-knock collector immediately before the Meet CTA click. */
export function markGoogleKnock(page: Page): void {
  const diagnostics = diagnosticsFor(page);
  diagnostics.armed = true;
  diagnostics.waitingTextSeen = false;
  diagnostics.disconnectedError = false;
  diagnostics.clientErrorPaths = [];
  installListeners(page, diagnostics);
}

/** Record that a waiting-room TEXT indicator was visible for this knock. */
export function noteGoogleWaitingRoomTextSeen(page: Page): void {
  diagnosticsFor(page).waitingTextSeen = true;
}

export function googleKnockSawDisconnectedError(page: Page): boolean {
  return diagnosticsFor(page).disconnectedError;
}

export function googleKnockSawWaitingRoomText(page: Page): boolean {
  return diagnosticsFor(page).waitingTextSeen;
}

export function googleKnockClientErrorPaths(page: Page): string[] {
  return [...diagnosticsFor(page).clientErrorPaths];
}

export async function readGoogleMeetRefusalHeadline(page: Page): Promise<string> {
  try {
    const text = await page.evaluate(() => {
      const clean = (value: string | null | undefined) =>
        String(value ?? "").replace(/\s+/g, " ").trim();
      const visible = (element: Element) => {
        const htmlElement = element as HTMLElement;
        const rect = htmlElement.getBoundingClientRect?.();
        const style = getComputedStyle(element);
        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          (!rect || (rect.width > 0 && rect.height > 0))
        );
      };

      const parts: string[] = [];
      const title = clean(document.title);
      if (title) parts.push(title);
      for (const element of Array.from(
        document.querySelectorAll('h1, h2, h3, [role="heading"], [aria-live]')
      )) {
        if (parts.length >= 5) break;
        if (!visible(element)) continue;
        const text = clean(element.textContent);
        if (text) parts.push(text);
      }
      if (parts.length === 0) {
        const body = clean(document.body?.innerText);
        if (body) parts.push(body.slice(0, 200));
      }
      return parts.join(" | ");
    });
    return sanitizeMeetingCode(text).replace(/\s+/g, " ").trim();
  } catch {
    return "";
  }
}

/** Emit the single L-1699 refusal diagnostic. The headline and every 4xx path
 *  are code-redacted and query-free before they reach the log line. */
export async function logGoogleKnockRefusal(page: Page, reason: string): Promise<void> {
  const headline = (await readGoogleMeetRefusalHeadline(page)).slice(0, MAX_HEADLINE_CHARS);
  logJSON({
    level: "warn",
    msg: "Google Meet refused the join knock",
    reason,
    headline,
    client_error_paths: googleKnockClientErrorPaths(page),
  });
}
