/** `GET /api/version` — what is serving, for a tab that wants to notice it was swapped underneath.
 *
 *  PRD decision 39: the founder no longer goes "out" while a container is replaced and "in" when it
 *  is back. The swap is invisible, which means the ONE thing the ritual bought — a person who knows
 *  the page in front of them is stale — has to be bought by the page itself.
 *
 *  It is a route of its own rather than a hop through the catch-all proxy, for three reasons:
 *    • the catch-all goes through the gateway with a per-user API key, so an unauthenticated tab
 *      (a sign-in screen, an expired session) could not poll it — and that is exactly the tab most
 *      likely to be looking at a stale bundle;
 *    • it must report BOTH halves. The pairing rule has two sides, and only this process knows the
 *      bundle's own build id;
 *    • it resolves `agent-api` through the compose network alias, which is the thing the blue/green
 *      swap moves. Reading it here means the answer changes the instant traffic is switched, with
 *      no restart of anything.
 *
 *  The server half is best-effort: an unreachable agent-api answers `server: null` rather than a
 *  500. A version probe that fails loudly would be a poll that paints an error banner every time a
 *  container is a second slow — the exact opposite of the point.
 */
import { NextResponse } from "next/server";
import { TERMINAL_AGENT_API, terminalBuild } from "../../../version";

export const dynamic = "force-dynamic";

export type ServerVersion = { sha: string; api: number };
export type VersionReport = {
  terminal: { build: string; agent_api: number };
  server: ServerVersion | null;
  /** false when the server answers a different contract number than this bundle was built for —
   *  the F55/F77 mismatch. The deploy script refuses to create it; if one is somehow live, the
   *  client shows the reload bar rather than pretending the pairing holds. */
  paired: boolean;
};

async function serverVersion(): Promise<ServerVersion | null> {
  const base = (process.env.AGENT_API_URL || "").replace(/\/$/, "");
  if (!base) return null;
  try {
    const r = await fetch(`${base}/api/version`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    if (!r.ok) return null;
    const j = (await r.json()) as { sha?: unknown; api?: unknown };
    if (typeof j?.sha !== "string" || typeof j?.api !== "number") return null;
    return { sha: j.sha, api: j.api };
  } catch {
    return null;
  }
}

export async function GET() {
  const server = await serverVersion();
  const body: VersionReport = {
    terminal: { build: terminalBuild(), agent_api: TERMINAL_AGENT_API },
    server,
    paired: server === null ? true : server.api === TERMINAL_AGENT_API,
  };
  return NextResponse.json(body, { headers: { "Cache-Control": "no-store" } });
}
