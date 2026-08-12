import { NextResponse } from "next/server";

import { RELEASE } from "@/lib/release-version";

/**
 * /api/version — what this deployment is ACTUALLY running.
 *
 * Two identities, from two different places, and neither is allowed to speak
 * for the other:
 *
 *   - `backend` is asked of the gateway (`GET {VEXA_API_URL}/version`) at
 *     request time. It is the only honest source for the backend version,
 *     because the backend is the thing that has it.
 *   - `ui` is this image's own build stamp (release-version.generated.json).
 *     It describes the UI and nothing else.
 *
 * What this replaces: the chip used to render PLATFORM_VERSION, a string
 * hand-set in the Helm values and expected to be kept "in lockstep with the
 * platform release staging actually serves". It was not kept in lockstep —
 * staging displayed 0.12.18 while the cluster served 0.12.22-rc.3 — and it
 * could not be, because nothing failed when it drifted.
 *
 * When the gateway cannot be reached, `backend` is null and `backendStatus` is
 * "unreachable". The UI then says "unknown". It never substitutes a
 * remembered, configured or compiled-in value: a version string that is wrong
 * is worse than one that is absent, because a reader acts on it.
 */

// Never prerendered, never cached: the whole point is that the answer can
// change under a fixed image.
export const dynamic = "force-dynamic";
export const revalidate = 0;

const BACKEND_TIMEOUT_MS = 2500;

type BackendVersion = {
  service?: string;
  version?: string;
  revision?: string;
};

export async function GET() {
  const apiUrl = (process.env.VEXA_API_URL || "").replace(/\/+$/, "");

  let backend: BackendVersion | null = null;
  let backendStatus: "ok" | "unreachable" | "unconfigured" = "unconfigured";
  let backendError: string | null = null;

  if (apiUrl) {
    backendStatus = "unreachable";
    try {
      const r = await fetch(`${apiUrl}/version`, {
        cache: "no-store",
        signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
      });
      if (r.ok) {
        const body = (await r.json()) as BackendVersion;
        // "unknown" is the gateway's honest answer for an unstamped deploy.
        // Carry it through as unreachable-equivalent rather than printing the
        // word "unknown" as if it were a version number.
        if (body && typeof body.version === "string" && body.version !== "unknown") {
          backend = { service: body.service, version: body.version, revision: body.revision };
          backendStatus = "ok";
        } else {
          backendError = "gateway did not know its own version";
        }
      } else {
        backendError = `gateway /version returned ${r.status}`;
      }
    } catch (e) {
      backendError = e instanceof Error ? e.message : String(e);
    }
  } else {
    backendError = "VEXA_API_URL is unset — no backend to ask";
  }

  return NextResponse.json(
    {
      component: "dashboard",
      fetchedAt: new Date().toISOString(),
      backendStatus,
      backend,
      backendError,
      ui: {
        version: RELEASE.version,
        releaseDate: RELEASE.releaseDate,
        source: RELEASE.source,
      },
    },
    { headers: { "cache-control": "no-store" } }
  );
}
