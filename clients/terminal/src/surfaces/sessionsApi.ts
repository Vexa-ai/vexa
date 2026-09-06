/** sessionsApi — the agent-sessions surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  Calls the ONE gateway edge under /api/sessions* with NO `subject`: the gateway injects X-User-Id and
 *  agent-api scopes the sessions to that user (P20). FAIL-LOUD (P18): a backend/network error THROWS
 *  (via apiClient) — never swallowed into an empty list. Proven in sessionsApi.test.ts. */
import { getJson } from "./apiClient";

/** One row of the session index — and, since Vexa-ai/vexa#1591, one row of the RAIL: the minutes
 *  chat list is derived from these rather than from one browser's `localStorage`.
 *
 *  `created` / `last_active` are EPOCH SECONDS on the wire (`_Sessions.list` stores floats); the
 *  string half of the type is kept because a caller reading this interface should not have to
 *  discover that from a runtime. `minutes/chats.ts::chatsFromSessions` coerces both. */
export interface SessionSummary {
  session: string;
  title?: string | null;
  /** WHAT THIS ROW IS CALLED (Vexa-ai/vexa#1602) — computed by the server so every client agrees.
   *
   *  `title` is what the session index STORED, which for a row minted before that rule is the first
   *  60 characters of a composed prompt: the founder's rail read `Active context: the u…` four
   *  times, plus `[vexa-job:extend…` and `[prep] They click…`. `label` is the one rule applied to
   *  it — the meeting's title, the scaffold's label, the act's label, or the person's own first
   *  words with every machinery preamble stripped.
   *
   *  Empty means NO NAME IS RECOVERABLE, never a name of the server's: "Chat" is this client's
   *  placeholder (`minutes/chats.ts` — `isPlaceholderLabel`) and a server that shipped it would
   *  outrank the reader's own rename in the merge. Absent means a server one release behind, which
   *  costs a fallback and never a broken rail. */
  label?: string | null;
  created?: number | string | null;
  last_active?: number | string | null;
  /** the mount set this chat is over, when the server knows it (a scaffold said so) */
  workspaces?: string[] | null;
  /** the record this chat was composed from — kind AND id together, or absent (F37) */
  scaffold?: { kind?: string | null; id?: string | null } | null;
  /** has a PERSON written here, or is every turn in it machinery? absent → treat as yes */
  touched?: boolean | null;
  /** THE MEETING THIS CHAT MADE — the row id, or null (Vexa-ai/vexa#1597).
   *
   *  Written server-side when a bot goes out from this session. A meeting somebody OPENED from the
   *  rail needs nothing here — its session is named `meet-<row>` and `chatsFromSessions` reads the
   *  ref off the id — but a chat that CREATED a meeting has an ordinary id, so this is the only
   *  place that binding exists. Without it the rail listed one meeting twice. */
  meeting?: string | null;
  /** …and the same meeting's NATIVE id, or null. On the wire because the binding answers to both
   *  addresses — the row is how the panel names a meeting, the native id is how meeting-api does.
   *  Not read here: this client takes a native id off the meetings list. */
  meeting_native?: string | null;
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
