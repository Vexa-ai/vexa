/** apiClient — the ONE fail-loud HTTP helper for the terminal's scoped data-access (P18).
 *
 *  A non-ok response (4xx/5xx — e.g. a backend 422, or the gateway down → 502) or a network failure
 *  THROWS `ApiError` with the status + detail, instead of being swallowed into empty data. That is the
 *  whole point: a failure must PROPAGATE to the surface and be shown to the user — never hidden as
 *  "no data" (which is exactly what made this session's stale-backend 422s and dry STT invisible).
 *  A legit-empty result (a 200 whose body is `[]`/`{}`) is NOT an error and returns normally. */
export class ApiError extends Error {
  constructor(public readonly status: number, public readonly detail: string, public readonly url: string) {
    super(`${url} → ${status || "network"}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

/** GET/POST… JSON, loud on failure. status 0 = the request never completed (network/DNS/abort). */
export async function getJson<T = unknown>(url: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch (e) {
    throw new ApiError(0, e instanceof Error ? e.message : "network error", url);
  }
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
