import { featureOn } from "../platform/features";
/** vocabulary — the words the product uses for its own surfaces, in ONE place.
 *
 *  Founder, 2026-09-02: a person's own workspace is a **DESK** — a personal desk, and a group desk
 *  for a group. Change `WORKSPACE_WORD` here and every preset that writes `{{workspace}}` follows
 *  on the next click; nothing is rebuilt, because the presets are read hot from `_global/asks/`.
 *
 *  The word carries the meaning, and it is why "private" was the wrong word for it: a desk is
 *  COMPANY KNOWLEDGE HELD BY ONE PERSON. The company's agents may read it for a meeting that person
 *  is in. `_system` — chats, sessions, settings — stays private and is not a desk.
 *
 *  Code paths, workspace slugs, API fields and storage keys keep saying "workspace" deliberately.
 *  Renaming those is a migration, and a naming decision should not cost a migration.
 *
 *  The flows runtime has the same constant on its side —
 *  `core/flows/src/flows_steps/mailtext.py: WORKSPACE_WORD` — because Python and TypeScript cannot
 *  share a literal here. The two lines name each other; together they are the whole rename.
 */

/** What a person's own workspace is CALLED to that person.
 *
 *  Founder, 2026-10-11: *"rename desk to personal to remove ambiguity"* — the chat's target chip
 *  already said "Writes to personal" while the navigator said "Desk". The word a person reads is
 *  now **Personal** ("your personal workspace" in a sentence); slugs, ids and fields still say
 *  `desk` where they did. */
export const WORKSPACE_WORD = "Personal";

/** "MY DESK", NOT "PERSONAL" (UI 7y, founder 2026-10-17; feature `shell2`): the personal workspace is
 *  DISPLAYED as "My desk" everywhere a person reads it — the target chip, "writes to My desk", cards,
 *  the tree. The slug stays `personal` (and `seed` for the older mount); only the display changes. */
export const DESK_DISPLAY = "My desk";
export const deskName = (): string => (featureOn("shell2") ? DESK_DISPLAY : WORKSPACE_WORD);
/** Is this slug the person's own desk (`personal`, the older `seed`, or none)? */
export const isDeskSlug = (slug?: string | null): boolean => !slug || slug === "personal" || slug === "seed";
/** A workspace's name as a person reads it: the desk is "My desk" under `shell2` whatever the
 *  server called it; any other workspace keeps its own name. */
export const workspaceDisplayName = (slug: string | null | undefined, name?: string | null): string =>
  isDeskSlug(slug) ? (featureOn("shell2") ? DESK_DISPLAY : (name || WORKSPACE_WORD)) : (name || String(slug));

/** What the ORGANISATION TIER (`_global`) is called where a person sees it — the header chip an
 *  admin can aim a chat at, and the `+` menu entry that puts it there (Vexa-ai/vexa#1616).
 *
 *  It is a FALLBACK, not the name: the registry answers for `_global` too, with the company's own
 *  name once the setup conversation writes one. This is what the chip wears in the gap before that
 *  — because the alternative fallback is the slug, and a header reading `_global` is the defect
 *  #1585/#1602 closed everywhere else. The word is its own README's heading. */
export const COMPANY_WORD = "Company";
