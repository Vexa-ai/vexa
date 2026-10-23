/** shareFlow — what pressing Enter on a message that @-mentions someone does (ADR-0048).
 *
 *  A mention of a person who is not in the chat yet is a question, asked once, inline, before the
 *  message goes: share this chat with them? `checkShare` decides whether to ask and with what facts
 *  (a private chat is shared with its history unless the owner starts a new chat instead; what the
 *  history read that only the owner may; for a routine's chat, which reads its runs would lose).
 *  `shareWith` makes the adds through the participants API, a person in the loop, and answers the
 *  chat the message now goes to. */
import {
  addParticipant, listParticipants, sharePreview, type ShareHistory, type SharePreview,
} from "./sessionsApi";
import type { Mention } from "./mentions";
import { tagPerson, type TagAnswer } from "./roomPeopleApi";

export type ShareCheck =
  | { kind: "send" }
  | { kind: "confirm"; people: Mention[]; owner: boolean; preview: SharePreview | null };

/** The HTTP status an API error carries (apiClient's `ApiError`), read by shape so this module does
 *  not depend on where the client lives. */
function statusOf(e: unknown): number | undefined {
  const s = (e as { status?: unknown } | null)?.status;
  return typeof s === "number" ? s : undefined;
}

type Api = { listParticipants: typeof listParticipants; sharePreview: typeof sharePreview;
  addParticipant: typeof addParticipant };
const real: Api = { listParticipants, sharePreview, addParticipant };

/** Ask, or just send? Ask when a mentioned person is not a participant yet. A deployment without
 *  the session store (503) has nobody to share with, so the message just goes. */
export async function checkShare(session: string, mentions: Mention[], api: Api = real): Promise<ShareCheck> {
  if (!mentions.length) return { kind: "send" };
  let inChat: string[] = [];
  let owner = true;
  try {
    const v = await api.listParticipants(session);
    inChat = v.participants.map((p) => p.principal);
    owner = (v.role ?? "owner") === "owner";
  } catch (e) {
    if (statusOf(e) === 503) return { kind: "send" };
    if (statusOf(e) !== 404) throw e;   // 404: a thread the store has not seen — yours alone
  }
  const people = mentions.filter((m) => !inChat.includes(m.subject));
  if (!people.length) return { kind: "send" };
  let preview: SharePreview | null = null;
  if (owner) {
    try { preview = await api.sharePreview(session); } catch (e) {
      if (statusOf(e) === 503) return { kind: "send" };
      throw e;
    }
  }
  return { kind: "confirm", people, owner, preview };
}

/** Add each person. `history` is the owner's choice for a chat only they are in (`all`: they join
 *  this chat, history included; `none`: a NEW shared chat, which every later add then goes to); null
 *  for a chat already shared. The answer is the chat the message goes to. */
export async function shareWith(session: string, people: Mention[], history: ShareHistory | null,
                                api: Api = real): Promise<string> {
  let target = session;
  let first = true;
  for (const p of people) {
    const who = first && history ? { subject: p.subject, history } : { subject: p.subject };
    const v = await api.addParticipant(target, who);
    if (first && history === "none" && v.session_id) target = v.session_id;
    first = false;
  }
  return target;
}

// ── ROOMS (0.13.4 S4, D21): a mention never silently changes who has access ─────────────────────
/** After a message that @-mentions people is posted IN A ROOM: what each tag does, from the server's
 *  four cases. A room member is only mentioned (nothing to show); anyone else yields the card only the
 *  tagger sees; a tagger's own "always" line answers `added_by_rule` (shown as a receipt with Undo).
 *  A server without the route (404/503) or a refusal shows nothing — the message is already sent. */
export async function checkRoomTags(session: string, mentions: Mention[],
                                    api: { tagPerson: typeof tagPerson } = { tagPerson }): Promise<TagAnswer[]> {
  const out: TagAnswer[] = [];
  for (const m of mentions) {
    try {
      const a = await api.tagPerson(session, { subject: m.subject, name: m.name });
      if (a.case === "added_by_rule" || a.card) out.push({ ...a, name: a.name || m.name });
    } catch {
      /* the message went; a card that cannot be built is not shown */
    }
  }
  return out;
}
