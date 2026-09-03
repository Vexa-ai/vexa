/** THE HIDE LIST — what a workspace shows a READER when it is shown as files (PRD decision 27.2,
 *  "human files only: no dot-directories, no machinery, no templates as pages").
 *
 *  ONE list, two consumers. The panel's breadcrumb listing and the navigator's tree are two views
 *  of the same question — "what is in this folder?" — and answering it twice is how they drift:
 *  a file hidden in the tree and shown in the listing is not a cosmetic difference, it is the
 *  reader learning that the rule is arbitrary. So both call `isMachinery` and neither carries a
 *  list of its own.
 *
 *  What is machinery: the agent's own instructions (`CLAUDE.md`), the liquid layer it runs on
 *  (`flows/`, `skills/`, `routines/`, `views/`, `policy/`) and the shapes pages are cut from
 *  (`kg/templates/`). None of it is a page a person reads; all of it is edited through the MCP
 *  verbs and the conversation.
 *
 *  What is NOT hidden: everything else, including files this build has never seen. A hide list that
 *  guesses ("looks generated") would hide the agent's own write-back the day it invents a folder.
 */

/** Root-level files that are machinery. Root-level ONLY — a `CLAUDE.md` a person wrote inside
 *  `drafts/` is a draft about CLAUDE.md, not the agent's instruction file. */
export const MACHINERY_FILES: readonly string[] = ["CLAUDE.md"];

/** Directories that are machinery, with everything under them. Written as paths from the workspace
 *  root, so `kg/templates` hides the templates without hiding `kg`. */
export const MACHINERY_DIRS: readonly string[] = [
  "flows",
  "skills",
  "routines",
  "views",
  "policy",
  "kg/templates",
];

/** A dot-file or anything under a dot-directory — `.git`, `.scaffolded`, `.claude/…`. */
export const isDotted = (path: string): boolean =>
  path.split("/").some((seg) => seg.startsWith("."));

/** Is this path — a file OR a directory, addressed from the workspace root — machinery?
 *
 *  Directories answer for themselves (`flows` is machinery) and for their contents
 *  (`flows/personal.md` is too), which is what lets one predicate serve a flat tree listing and a
 *  one-level folder listing without either translating for the other. */
export function isMachinery(path: string): boolean {
  const p = String(path ?? "").replace(/^\/+/, "").replace(/\/+$/, "");
  if (!p) return true;
  if (isDotted(p)) return true;
  if (MACHINERY_FILES.includes(p)) return true;
  return MACHINERY_DIRS.some((d) => p === d || p.startsWith(`${d}/`));
}

/** The paths a reader is shown, in the order they came. */
export const humanPaths = (paths: readonly string[]): string[] => paths.filter((p) => !isMachinery(p));

/** One entry of a folder listing: the breadcrumb holds a prefix and a bare name, and the rule is
 *  about the whole path, so this is where the two are joined. */
export const isMachineryEntry = (prefix: string, name: string): boolean =>
  isMachinery(prefix ? `${prefix}/${name}` : name);

/** `template: true` in a page's own frontmatter — the third clause of decision 27.2, and the only
 *  one that cannot be answered from a path.
 *
 *  `GET /api/workspace/tree` returns paths and nothing else, so the tree and the breadcrumb listing
 *  apply the path rules above and this predicate stays available for any caller that HOLDS a body
 *  (the panel does, once a document is open). It lives here rather than beside its one caller so
 *  that the hide list is one file, not one file plus a rule someone else keeps.
 */
export function frontmatterSaysTemplate(body: string | null | undefined): boolean {
  if (!body) return false;
  const m = /^﻿?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(body);
  if (!m) return false;
  return /^[ \t]*template[ \t]*:[ \t]*(?:true|yes)[ \t]*$/im.test(m[1]);
}
