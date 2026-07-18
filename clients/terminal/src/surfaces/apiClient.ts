/** apiClient — the ONE fail-loud HTTP helper for the terminal's scoped data-access (P18).
 *
 *  A non-ok response (4xx/5xx — e.g. a backend 422, or the gateway down → 502) or a network failure
 *  THROWS `ApiError` with the status + detail, instead of being swallowed into empty data. That is the
 *  whole point: a failure must PROPAGATE to the surface and be shown to the user — never hidden as
 *  "no data" (which is exactly what made this session's stale-backend 422s and dry STT invisible).
 *  A legit-empty result (a 200 whose body is `[]`/`{}`) is NOT an error and returns normally.
 *
 *  Being the single client→API chokepoint, this is also where we report API usage to Google Analytics
 *  (one `api_request` event per call: normalized endpoint · method · status · ok). GA is opt-in, so the
 *  track() call no-ops unless a measurement id was configured. NOTE: this captures only WEB-CLIENT calls
 *  — the authoritative cross-caller API-usage signal is the gateway's per-request logs. */
import { track, endpointLabel } from "@/app/analytics";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly detail: string, public readonly url: string) {
    super(`${url} → ${status || "network"}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

/** What a surface renders for a failure: the user-truth headline + the full plumbing string.
 *  `headline` is user vocabulary; `detail` is the untranslated fault (ApiError.message / the raw
 *  error text) for the operator channel — a `title=` affordance and the browser console. */
export interface PresentedError { headline: string; detail: string }

const NETWORK_HEADLINE = "Couldn't reach the Vexa server — check that the stack is running.";
const GENERIC_HEADLINE = "Something went wrong — details are in the browser console.";

// A fetch()-level network failure surfaces as one of these engine-specific messages.
const NETWORK_MESSAGE = /failed to fetch|networkerror|load failed|network request failed/i;

/** Is a backend `detail` a human sentence we can show verbatim (the backend's own user-facing
 *  reason), rather than a serialized payload? Deliberately conservative: prose has a space and
 *  doesn't open like JSON/markup. */
function isProse(detail: string): boolean {
  const t = detail.trim();
  return t.length > 0 && t.length <= 300 && !/^[{[<]/.test(t) && t.includes(" ");
}

/** The presenter seam (issue #533): every terminal surface renders `presentError(e).headline`,
 *  never `e.message`. Maps the STRUCTURED fields of an `ApiError` to user vocabulary; the typed
 *  plumbing is preserved intact on the returned `detail` AND echoed to the console (P18's
 *  observable channel keeps the full string — presentation never mutates the error). */
export function presentError(e: unknown): PresentedError {
  if (e instanceof ApiError) {
    const detail = e.message;
    console.warn("api failure", detail);
    if (e.status === 0) return { headline: NETWORK_HEADLINE, detail };
    if (e.status === 502 || e.status === 504) return { headline: "The Vexa server can't reach a backend service right now.", detail };
    if (e.status === 401) return { headline: "Your API key was rejected — sign in again.", detail };
    if (e.status === 403) return { headline: "Your key doesn't have access to this.", detail };
    if (e.status === 429) return { headline: "Rate limit hit — try again in a moment.", detail };
    // Remaining 4xx/5xx: a prose `detail` is the backend's own user-facing reason — pass it
    // through VERBATIM (e.g. a typed transcription 503). A payload-shaped detail stays operator-only.
    if (isProse(e.detail)) return { headline: e.detail.trim(), detail };
    return { headline: `The request failed (${e.status}).`, detail };
  }
  if (e instanceof Error) {
    const detail = e.message || String(e);
    console.warn("api failure", detail);
    if (NETWORK_MESSAGE.test(detail)) return { headline: NETWORK_HEADLINE, detail };
    return isProse(detail) ? { headline: detail.trim(), detail } : { headline: GENERIC_HEADLINE, detail };
  }
  const detail = String(e);
  console.warn("api failure", detail);
  return { headline: GENERIC_HEADLINE, detail };
}

/** GET/POST… JSON, loud on failure. status 0 = the request never completed (network/DNS/abort). */
export async function getJson<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const usage = (status: number, ok: boolean) =>
    track("api_request", { endpoint: endpointLabel(url), method, status, ok });

  let r: Response;
  try {
    r = await fetch(url, init);
  } catch (e) {
    usage(0, false);  // network/DNS/abort — never reached the gateway
    throw new ApiError(0, e instanceof Error ? e.message : "network error", url);
  }
  usage(r.status, r.ok);
  if (!r.ok) {
    let detail = "";
    try {
      const b = (await r.json()) as { detail?: unknown; error?: unknown };
      const d = b?.detail ?? b?.error;
      detail = typeof d === "string" ? d : d != null ? JSON.stringify(d).slice(0, 200) : "";
    } catch {
      /* body wasn't JSON — the status alone is the signal */
    }
    throw new ApiError(r.status, detail, url);
  }
  return (await r.json()) as T;
}
