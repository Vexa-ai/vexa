/**
 * Meeting-URL redaction — what a meeting link may look like once it leaves the join itself.
 *
 * A meeting link is a credential: Zoom's `pwd`/`password`, Teams' `p`, a Jitsi `jwt`, Zoom's host
 * `zak` all let whoever reads the line into the call. Logs ship off-box and error strings become a
 * meeting's stored `reason` and its webhook, so every line that names a URL names it through here.
 *
 * Scheme, host and path stay (the meeting id lives there, which is what makes the line useful);
 * the VALUE of every secret-named query or fragment parameter becomes `***`, and credentials in the
 * authority become `***@`. Pure string work, never a URL parser: the Python twin
 * (`meeting_url_redaction.py`) must produce the same bytes, and both are held to one vector table
 * (`meeting-url-redaction-vectors.json`, gate:fact-parity).
 */

const SECRET_NAMES = new Set(["pwd", "password", "passcode", "p", "tk", "zak", "jwt", "token", "sig", "signature"]);
const SECRET_PARTS = ["key", "secret", "token", "password", "passcode"];
const MASK = "***";
const URL_IN_TEXT = /https?:\/\/[^\s<>"'`]+/g;
const TRAILING = ".,;:)!?";

function isSecretName(name: string): boolean {
  // `&amp;` (a URL inside HTML) separates parameters like `&`
  const n = name.toLowerCase().replace(/^amp;/, "");
  return SECRET_NAMES.has(n) || SECRET_PARTS.some((part) => n.includes(part));
}

function maskParams(params: string): string {
  return params
    .split("&")
    .map((piece) => {
      const eq = piece.indexOf("=");
      if (eq < 0) return piece;
      const name = piece.slice(0, eq);
      const value = piece.slice(eq + 1);
      return value && isSecretName(name) ? `${name}=${MASK}` : piece;
    })
    .join("&");
}

function maskAuthority(base: string): string {
  const sep = base.indexOf("://");
  if (sep < 0) return base;
  const start = sep + 3;
  const slash = base.indexOf("/", start);
  const end = slash < 0 ? base.length : slash;
  const authority = base.slice(start, end);
  const at = authority.lastIndexOf("@");
  if (at < 0) return base;
  return `${base.slice(0, start)}${MASK}${authority.slice(at)}${base.slice(end)}`;
}

/** One URL with every secret parameter value masked; host, path and meeting id kept. */
export function redactMeetingUrl(url: string): string {
  const text = String(url ?? "");
  const hash = text.indexOf("#");
  const beforeHash = hash < 0 ? text : text.slice(0, hash);
  const fragment = hash < 0 ? null : text.slice(hash + 1);
  const q = beforeHash.indexOf("?");
  const base = q < 0 ? beforeHash : beforeHash.slice(0, q);
  const query = q < 0 ? null : beforeHash.slice(q + 1);
  let out = maskAuthority(base);
  if (query !== null) out += `?${maskParams(query)}`;
  if (fragment !== null) out += `#${maskParams(fragment)}`;
  return out;
}

/** Every http(s) URL inside free text (an error message, a log line) redacted in place. */
export function redactUrlsIn(text: unknown): string {
  return String(text ?? "").replace(URL_IN_TEXT, (match) => {
    let end = match.length;
    while (end > 0 && TRAILING.includes(match[end - 1])) end -= 1;
    return redactMeetingUrl(match.slice(0, end)) + match.slice(end);
  });
}

/**
 * `page.goto(url).catch(rethrowRedacted)`: Playwright quotes the URL it was navigating to in its
 * own message (`navigating to "https://…?pwd=…"`), and that message becomes the meeting's stored
 * reason. The same error object is rethrown — its class and name still classify it — with the
 * URLs in its message and stack redacted.
 */
export function rethrowRedacted(error: unknown): never {
  if (error instanceof Error) {
    error.message = redactUrlsIn(error.message);
    if (typeof error.stack === "string") error.stack = redactUrlsIn(error.stack);
    throw error;
  }
  throw new Error(redactUrlsIn(error));
}
