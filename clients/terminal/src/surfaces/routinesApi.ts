/** routinesApi — the Routines surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  Every call goes to the ONE gateway edge under /api/routines* and carries NO `subject`: the gateway
 *  injects X-User-Id and agent-api derives the user's scope from it (P20). FAIL-LOUD (P18): a backend
 *  error or network failure THROWS (via apiClient) instead of being swallowed into an empty list — the
 *  surface catches it and shows the error, so a failure is never hidden as "no routines". */
import { getJson } from "./apiClient";

export interface Routine {
  id: string; name: string; cron: string; plan_summary?: string; enabled: boolean;
  /** Written where no person stood behind it (an agent's write on the workspace, or a run with nobody
   *  watching): not armed until the person confirms it. */
  pending_confirmation?: boolean;
}

export async function listRoutines(): Promise<Routine[]> {
  const data = await getJson<{ routines?: Routine[] }>(`/api/routines`);
  return (data.routines ?? []).map((r) => ({ ...r, name: r.name, enabled: r.enabled }));
}

export async function deleteRoutine(id: string): Promise<void> {
  await getJson(`/api/routines/${id}`, { method: "DELETE" });
}

/** The person stands behind this routine as its file reads now — arms one shown as pending. */
export async function confirmRoutine(name: string): Promise<void> {
  await getJson(`/api/routines/${encodeURIComponent(name)}/confirm`, { method: "POST" });
}

export async function setRoutineEnabled(name: string, enabled: boolean): Promise<void> {
  await getJson(`/api/routines/${encodeURIComponent(name)}/enabled`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}
