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

/** What a person's own workspace is CALLED to that person. */
export const WORKSPACE_WORD = "desk";
