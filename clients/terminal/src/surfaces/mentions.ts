/** mentions — the composer's @-mentions, as pure functions (ADR-0048).
 *
 *  Typing `@` opens a picker of the people the server says the caller may mention (they share a
 *  workspace) and the agents a chat may address. Choosing one writes `@Label ` into the text and
 *  remembers WHO it was — the account id — so the message names exactly one account and nothing is
 *  ever resolved by guessing from a name. A mention whose `@Label` the person has since deleted is
 *  not sent. */
import type { MentionablePerson } from "./sessionsApi";

/** One person picked into this message. `label` is what the text says after the `@`. */
export interface Mention { subject: string; label: string; name: string;
  /** what picking them is for (founder: "to tag and to invite to a call"): a tag by default; `chat`
   *  invites them into this chat (with the confirm), `call` also starts a call they are rung into */
  intent?: "tag" | "chat" | "call" }

/** A picker row: a person, or an agent (`@vexa` — "agent, answer this"). */
export type PickerRow = { kind: "person"; person: MentionablePerson; label: string }
  | { kind: "agent"; name: string; label: string };

const QUERY = /(^|[\s(])@([\p{L}\p{N}._-]{0,40})$/u;
const MAX_ROWS = 8;

/** The `@query` being typed right before the caret, or null. */
export function activeMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, Math.max(0, Math.min(caret, text.length)));
  const m = QUERY.exec(before);
  if (!m) return null;
  return { start: before.length - m[2].length - 1, query: m[2] };
}

/** What a person's mention reads as: their first name, else the address's local part — one token. */
export function labelOf(p: MentionablePerson): string {
  const first = (p.name || "").trim().split(/\s+/)[0] || (p.email || "").split("@")[0] || p.subject;
  return first.replace(/[^\p{L}\p{N}._-]/gu, "") || p.subject;
}

/** "2 accounts" when one human confirmed several logins as theirs (D35: the picker lists Persons);
 *  "2 accounts?" while that is only the records' guess — a hint, and a grant then reaches only the
 *  account picked. "" for one login. The server sends `accounts_confirmed` as `accountsConfirmed`
 *  on the known set and as `accounts_confirmed` on the mentionable set. */
export function accountsLabel(p: Pick<MentionablePerson, "accounts" | "accountsConfirmed"> & { accounts_confirmed?: boolean }): string {
  const n = Number(p.accounts || 0);
  if (n <= 1) return "";
  return (p.accountsConfirmed ?? p.accounts_confirmed) ? `${n} accounts` : `${n} accounts?`;
}

/** The rows the picker shows for `query`: agents first when they match, then people whose name or
 *  address contains it. Never anyone the server did not list. */
export function pickerRows(people: MentionablePerson[], agents: { name: string }[], query: string): PickerRow[] {
  const q = query.toLowerCase();
  const rows: PickerRow[] = [];
  for (const a of agents) if (a.name.toLowerCase().startsWith(q)) rows.push({ kind: "agent", name: a.name, label: a.name });
  for (const p of people) {
    const hay = `${p.name || ""} ${p.email || ""} ${p.role || ""}`.toLowerCase();
    if (!q || hay.includes(q)) rows.push({ kind: "person", person: p, label: labelOf(p) });
  }
  return rows.slice(0, MAX_ROWS);
}

/** The text with the `@query` at `at` replaced by `@label `, and where the caret goes. */
export function insertMention(text: string, at: { start: number; query: string }, label: string): { text: string; caret: number } {
  const end = at.start + 1 + at.query.length;
  const inserted = `@${label} `;
  return { text: text.slice(0, at.start) + inserted + text.slice(end), caret: at.start + inserted.length };
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The picked mentions the text still carries (`@Label` as a word), each once. */
export function mentionsInText(text: string, picked: Mention[]): Mention[] {
  const seen = new Set<string>();
  return picked.filter((m) => {
    if (seen.has(m.subject)) return false;
    const there = new RegExp(`(^|[^\\p{L}\\p{N}_.@-])@${escape(m.label)}(?![\\p{L}\\p{N}_-])`, "u").test(text);
    if (there) seen.add(m.subject);
    return there;
  });
}

/** "Anna", "Anna and Joe", "Anna, Joe and Sam". */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A person who is not a Vexa account, picked from @: a reference, not a mention — their page's
 *  `[[Name]]` when they have one (a chip with their hover card), else their name. Nothing is sent. */
export function insertReference(text: string, at: { start: number; query: string }, p: MentionablePerson | null): { text: string; caret: number } {
  const end = at.start + 1 + at.query.length;
  const name = (p?.name || "").trim() || "someone";
  const inserted = `${p?.page ? `[[${name}]]` : name} `;
  return { text: text.slice(0, at.start) + inserted + text.slice(end), caret: at.start + inserted.length };
}

/** The words the agent gets when the person invites someone who is not on Vexa to this chat's call:
 *  a draft with the call's link, never sent without their confirm. */
export function callInviteDraft(p: MentionablePerson): { say: string; prompt: string } {
  const to = p.email ? `${p.name} <${p.email}>` : p.name;
  return {
    say: `Invite ${p.name} to this call`,
    prompt: `Start a call in this chat if none is running, then draft a short message to ${to} inviting them to it, with the call's link. `
      + "Leave it as a draft for me to review and send nothing until I give my explicit go on the final text.",
  };
}
