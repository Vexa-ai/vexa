/** sessionsApi — the agent-sessions surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  Calls the ONE gateway edge under /api/sessions* with NO `subject`: the gateway injects X-User-Id and
 *  agent-api scopes the sessions to that user (P20). FAIL-LOUD (P18): a backend/network error THROWS
 *  (via apiClient) — never swallowed into an empty list. Proven in sessionsApi.test.ts. */
import { getJson } from "./apiClient";

export interface SessionSummary {
  session: string;
  title?: string | null;
  created?: string | null;
  last_active?: string | null;
}

export interface SessionHistory { turns: { role: string; text: string; ops?: unknown[]; commit?: unknown }[] }

export async function listSessions(): Promise<SessionSummary[]> {
  const data = await getJson<{ sessions?: SessionSummary[] }>(`/api/sessions`);
  return data.sessions ?? [];
}

export async function sessionHistory(session: string): Promise<SessionHistory> {
  const data = await getJson<{ turns?: SessionHistory["turns"] }>(`/api/sessions/${encodeURIComponent(session)}/history`);
  return { turns: data.turns ?? [] };
}
