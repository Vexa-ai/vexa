/** vocabulary — the words the product uses for its own surfaces, in ONE place.
 *
 *  Founder decision 21, 2026-09-02: a person's own workspace is NOT private and will be RENAMED.
 *  `WORKSPACE_WORD` is a PLACEHOLDER ("desk") until he picks the word. Change it here and every
 *  preset that writes `{{workspace}}` follows on the next click — nothing is rebuilt, because the
 *  presets are read hot from `_global/asks/`.
 *
 *  Code paths, workspace slugs, API fields and storage keys keep saying "workspace" deliberately.
 *  Renaming those is a migration, and a naming decision should not cost a migration.
 *
 *  The flows runtime has the same constant on its side —
 *  `core/flows/src/flows_steps/mailtext.py: WORKSPACE_WORD` — because Python and TypeScript cannot
 *  share a literal here. The two lines name each other; together they are the whole rename.
 */

/** What a person's own workspace is CALLED to that person. Placeholder until the founder picks. */
export const WORKSPACE_WORD = "desk";
