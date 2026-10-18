/** sessionsApi — the agent-sessions surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  Calls the ONE gateway edge under /api/sessions* with NO `subject`: the gateway injects X-User-Id and
 *  agent-api scopes the sessions to that user (P20). FAIL-LOUD (P18): a backend/network error THROWS
 *  (via apiClient) — never swallowed into an empty list. Proven in sessionsApi.test.ts. */
import { getJson } from "../data/apiClient";
import { readFault, type Fault } from "./faults";

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
  /** a routine's thread (ADR-0046): which of the person's flows runs its turns here, or null */
  routine?: { flow?: string | null; scope?: string | null; version?: number | null } | null;
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
  /** ADR-0048 — the session store. The store id of this conversation (`s-…`), when it has one. */
  session_id?: string | null;
  /** how many participants (people and agents) the conversation has; 1 is a private chat */
  participants?: number | null;
  /** a conversation somebody else owns and added the caller to — or one of the caller's own that
   *  others were added to. Absent on a server without the store. */
  shared?: boolean | null;
  /** the caller's role in it: `owner`, `member` (may post) or `viewer` (reads only) */
  role?: "owner" | "member" | "viewer" | null;
  owner?: string | null;
}

export interface SessionHistory {
  turns: { role: string; text: string; ops?: unknown[]; commit?: unknown }[];
  /** ADR-0048 S5 — the conversation's last reply was produced but the store refused to save it
   *  (`session-store` · `session_conflict` / `session_unavailable`). Shown until the next save. */
  fault: Fault | null;
}

export async function listSessions(): Promise<SessionSummary[]> {
  const data = await getJson<{ sessions?: SessionSummary[] }>(`/api/sessions`);
  return data.sessions ?? [];
}

export async function sessionHistory(session: string): Promise<SessionHistory> {
  const data = await getJson<{ turns?: SessionHistory["turns"]; fault?: unknown }>(`/api/sessions/${encodeURIComponent(session)}/history`);
  return { turns: data.turns ?? [], fault: readFault(data.fault) };
}

/** One participant of a conversation (ADR-0048). `principal` is a person's id or an agent's name. */
export interface Participant { principal: string; kind: "human" | "agent"; role: "owner" | "member" | "viewer"; added?: number }
export interface ParticipantsView { session_id: string; owner?: string; you?: string; role?: string | null; participants: Participant[]; names?: Record<string, string> }

/** Who is in this conversation. A thread the store has never seen answers 404 — the caller treats
 *  that as "just you", which is what it is. */
export async function listParticipants(session: string): Promise<ParticipantsView> {
  return getJson<ParticipantsView>(`/api/sessions/${encodeURIComponent(session)}/participants`);
}

export type ShareHistory = "all" | "none" | `from:${number}`;

/** Add a person by email (or an agent by name). The owner of a chat only they are in names `history`
 *  (`all` when omitted); anyone else adding a person starts a new chat.
 *  Adding someone shares this conversation and nothing else — no workspace. */
export async function addParticipant(session: string, who: { subject?: string; email?: string; agent?: string;
                                       /** the owner's choice when a chat only they are in is shared:
                                        *  `all` (in place, history included) · `from:<turn>` · `none` (a new chat) */
                                       history?: ShareHistory },
                                     role: "member" | "viewer" = "member"): Promise<ParticipantsView> {
  return getJson<ParticipantsView>(`/api/sessions/${encodeURIComponent(session)}/participants`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...who, role }),
  });
}

/** Remove a participant (owner) or leave (naming yourself). */
export async function removeParticipant(session: string, principal: string): Promise<{ removed: boolean; participants?: Participant[] }> {
  return getJson(`/api/sessions/${encodeURIComponent(session)}/participants/${encodeURIComponent(principal)}`,
                 { method: "DELETE" });
}

/** Messages posted to the conversation that the agent was not asked to answer, oldest first. */
export async function sessionMessages(session: string): Promise<{ seq: number; author: string; text: string; created: number }[]> {
  const data = await getJson<{ messages?: { seq: number; author: string; text: string; created: number }[] }>(
    `/api/sessions/${encodeURIComponent(session)}/messages`);
  return data.messages ?? [];
}

/** The chat's lines, who is asking (so they read "You"), the conversation's version (it moves when
 *  anyone's turn lands) and how each person is named. */
export type RoomShowingWire = { by: string; workspace: string; path: string; title: string; seq: number } | null;
export type RoomTurnWire = "thinking" | "nothing" | null;
export type RoomStepWire = { tool: string; file_path?: string } | null;
export async function sessionRoomLines(session: string): Promise<{ you: string; version: number; names: Record<string, string>; messages: { seq: number; author: string; text: string; created: number }[]; showing: RoomShowingWire; agent_turn: RoomTurnWire; agent_step: RoomStepWire }> {
  const data = await getJson<{ you?: string; version?: number; names?: Record<string, string>; messages?: { seq: number; author: string; text: string; created: number }[]; showing?: RoomShowingWire; agent_turn?: RoomTurnWire; agent_step?: RoomStepWire }>(
    `/api/sessions/${encodeURIComponent(session)}/messages`);
  return { you: String(data.you ?? ""), version: Number(data.version ?? 0), names: data.names ?? {}, messages: data.messages ?? [], showing: data.showing ?? null,
           agent_turn: data.agent_turn ?? null, agent_step: data.agent_step ?? null };
}

/** A person the caller may @-mention: someone they share a workspace with (ADR-0048). `subject` is
 *  their stable account id — what a mention carries, so it names exactly one account. */
export interface MentionablePerson {
  subject: string; name: string; email?: string; workspaces?: string[];
  /** the people set (`/api/people/known`): their stable key, whether they are a Vexa account (can be
   *  invited into a chat or rung into a call), their page on the caller's desk, how they are known */
  key?: string; vexa?: boolean; page?: string | null; how?: string[]; howWords?: string; role?: string;
}

/** A person the caller has interacted with (`GET /api/people/known`), for the home's People strip and
 *  the @ picker — ONE server set for both (founder 2026-10-15). */
export interface KnownPerson {
  key: string; name: string; subject: string | null; vexa: boolean; how: string[]; how_words: string;
  last: number | null; page: string | null;
  person: { name: string; initials?: string; avatar?: string; photo?: string; logo?: string; role?: string; email?: string; presence?: string };
}
export async function listKnownPeople(q = ""): Promise<{ people: KnownPerson[]; count: number }> {
  const data = await getJson<{ people?: KnownPerson[]; count?: number }>(`/api/people/known${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  return { people: data.people ?? [], count: data.count ?? (data.people ?? []).length };
}
/** The known people as @-picker rows: a Vexa account by its id, anyone else as a reference. */
export function knownAsMentionable(rows: KnownPerson[]): MentionablePerson[] {
  return rows.map((r) => ({ subject: r.subject ?? "", name: r.name, email: r.person.email, key: r.key, vexa: r.vexa && !!r.subject,
    page: r.page, how: r.how, howWords: r.how_words, role: r.person.role }));
}

/** The people (and agents) the composer's @ picker offers. The server answers only people the caller
 *  shares a group workspace with; nobody else is ever named. */
export async function listMentionable(): Promise<{ people: MentionablePerson[]; agents: { name: string }[] }> {
  const data = await getJson<{ people?: MentionablePerson[]; agents?: { name: string }[] }>(`/api/people/mentionable`);
  return { people: data.people ?? [], agents: data.agents ?? [] };
}

/** What adding a person here would do, for the confirmation the owner reads first (owner only). */
export interface SharePreview {
  session_id: string;
  /** a chat only its owner is in: the owner chooses what of its history the person sees (`history`) */
  forks: boolean;
  history?: { options: string[]; default: "all"; turns: number };
  /** a routine's chat: the routine stays its owner's */
  routine: { name: string } | null;
  /** the owner's private reads the history holds (and a routine's runs would lose once shared), e.g. "Gmail" */
  private_reads: string[];
  participants: number;
}

export async function sharePreview(session: string): Promise<SharePreview> {
  return getJson<SharePreview>(`/api/sessions/${encodeURIComponent(session)}/share-preview`);
}

/** Bind a meeting started from a chat (a voice note, a call) to that chat — the binding a bot sent
 *  from the chat makes on the server by itself (founder, 2026-10-13). */
export async function bindMeetingToChat(session: string, meeting: string): Promise<void> {
  await getJson(`/api/chat/meeting`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, meeting }),
  });
}
