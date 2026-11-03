/** versionWatch — the decision half of the reload bar, with no React and no timers in it.
 *
 *  PRD decision 39 removed the "out / in" ritual: containers are now replaced beside the running
 *  ones and traffic is switched with nobody asked to leave. What the ritual used to guarantee is
 *  that the founder never sat in front of a stale tab. This module is that guarantee, restated as a
 *  comparison a test can drive: hold the FIRST reading as the baseline, and answer whether a later
 *  reading means the page in front of the person is no longer the deployment behind it.
 *
 *  Three rules, and each one is a defect we watched happen:
 *
 *  1. **The server moved** — `sha` changed. F20: a container swapped under an open tab, the
 *     in-flight request said "fetch failed", and the person read it as the product breaking.
 *  2. **The bundle moved** — the terminal's own build id changed, so a reload would fetch different
 *     code than the tab is running. This is what makes the bar fire for a terminal-only swap.
 *  3. **The pair broke** — the server answers a contract number this bundle was not built for
 *     (F55/F77). The swap refuses to create that state, so seeing it means something else did;
 *     offering a reload is the only move a client has.
 *
 *  What is deliberately NOT a change: a reading with no server half. agent-api is briefly
 *  unreachable during a swap by construction, and `server: null` means "no reading", never "a
 *  different one". Treating it as a change would paint the bar every single swap, a second before
 *  the swap succeeds — a boy-who-cried-wolf bar is worse than none, because the person stops
 *  reading it exactly when it finally matters.
 */

export type ServerVersion = { sha: string; api: number };
export type VersionReport = {
  terminal: { build: string; agent_api: number };
  server: ServerVersion | null;
  paired: boolean;
};

/** The reading the tab loaded with. `sha` is null until agent-api has answered once. */
export type Baseline = { build: string; sha: string | null };

export function baselineOf(report: VersionReport): Baseline {
  return { build: report.terminal.build, sha: report.server?.sha ?? null };
}

/** Fold a later reading into the baseline: a first server answer FILLS the unknown half; it never
 *  overwrites a known one (that difference is the news, and losing it would silence the bar). */
export function foldBaseline(baseline: Baseline, report: VersionReport): Baseline {
  if (baseline.sha === null && report.server) return { ...baseline, sha: report.server.sha };
  return baseline;
}

/** Is the page in front of the person no longer the deployment behind it? */
export function reloadOffered(baseline: Baseline, report: VersionReport): boolean {
  if (report.terminal.build !== baseline.build) return true;
  if (!report.paired) return true;
  if (baseline.sha !== null && report.server !== null && report.server.sha !== baseline.sha) return true;
  return false;
}

/** Fetch one reading. Never throws: a failed poll is silence, not a banner. */
export async function readVersion(fetcher: typeof fetch = fetch): Promise<VersionReport | null> {
  try {
    const r = await fetcher("/api/version", { cache: "no-store" });
    if (!r.ok) return null;
    const j = (await r.json()) as Partial<VersionReport>;
    if (!j || typeof j.terminal?.build !== "string" || typeof j.terminal?.agent_api !== "number") return null;
    return {
      terminal: { build: j.terminal.build, agent_api: j.terminal.agent_api },
      server: j.server && typeof j.server.sha === "string" && typeof j.server.api === "number"
        ? { sha: j.server.sha, api: j.server.api }
        : null,
      paired: j.paired !== false,
    };
  } catch {
    return null;
  }
}

/** How often a tab asks. 60s is the number in decision 39: long enough to be free, short enough
 *  that a person who walked away for a coffee comes back to a current page. Focus is the other
 *  trigger, and it is the one that actually fires in practice — a swap happens while the tab is
 *  in the background far more often than while it is being read. */
export const POLL_MS = 60_000;
