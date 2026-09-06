/** THE NAVIGATOR'S DATA — which workspaces exist, what is in one, and what a filter matches.
 *
 *  Every function that decides something is PURE and exported: the component fetches, these decide.
 *  That split is why the ordering rule, the greying rule and the 50-hit cap are testable without a
 *  DOM, and why swapping the SOURCE of workspace names later changes one call rather than a screen.
 *
 *  ONLY WHAT THE READER CAN OPEN, AND NEVER AN ID. Founder ruling 2026-09-06 on this rail
 *  (Vexa-ai/vexa#1585): *"no point of showing workspaces not available to you"*. The two rows that
 *  read `btdl3lds34 — not available to you` were ids the reader's own desk still links to, of
 *  workspaces that are now `gone`; the rail was mining those links for a listing. It no longer
 *  does. The list is the mount table plus the shared-membership index, and each membership is
 *  resolved through `/api/workspaces/{id}` for the NAME it wears — dropped when that answers
 *  anything but `readable`. A workspace the reader cannot read is ABSENT, not a grey row.
 *
 *  Decision 26.3's greyed chip is untouched where it was ruled: on a LINK someone followed, in
 *  `ui-kit/WsLink`, where "not yours" answers a question the reader asked. A listing asks nothing.
 */
import type { ActiveMount, Membership } from "../surfaces/workspaceApi";
import { listSharedMemberships, listWorkspaceTree, readActiveSet, readWorkspaceById } from "../surfaces/workspaceApi";
import { GLOBAL_SLUG, humanPaths } from "./machinery";

/** Desk first, then `_global`, then the groups — decision 27.1's order, and it is not alphabetical
 *  by accident: the reader's own desk is where they write, `_global` is what the company shares. */
export type NavKind = "desk" | "global" | "group";

export interface NavWorkspace {
  /** stable list key — the slug, or `desk` for the reader's own (which is addressed with NO slug) */
  key: string;
  /** what `listWorkspaceTree`/`readWorkspaceFile` take; `undefined` = the caller's own workspace */
  slug?: string;
  name: string;
  kind: NavKind;
}

/** A membership as the LIST needs it: the id it is addressed by, plus the name the registry knows
 *  for it. `name` absent → the id is the only name we have, and it is still the reader's to open. */
export interface NavMembership { workspace_id: string; name?: string | null }

/** Re-exported from `./machinery`, which owns the literal: the hide list is the one module that
 *  HAS to know which workspace `_global` is (its `flows/` is content there, machinery everywhere
 *  else), so it holds the constant and this one borrows it rather than keeping a second copy. */
export { GLOBAL_SLUG };
/** Always mounted, never listed as a place to read files (PRD §7: per-user private machinery). */
const SYSTEM_SLUG = "_system";

/** The mount a no-slug read reaches — the reader's own desk. It does not announce itself by name
 *  (the server calls it `seed`), so the marks are `primary`, the subject, and the mount path.
 *  Same test `ui-kit/docLinks` applies; duplicated deliberately rather than exported across a
 *  layer boundary that has no other reason to exist. */
const isHomeMount = (m: ActiveMount, subject?: string): boolean =>
  m.primary || m.slug === subject || (!!subject && m.path.endsWith(`/${subject}`));

const byName = (a: NavWorkspace, b: NavWorkspace) =>
  a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.key.localeCompare(b.key);

/** THE LIST, in the order it is shown.
 *
 *  Every row is a workspace this reader mounts or is a member of — the only two ways in. Nothing
 *  else reaches the rail, so there is no row here that a click cannot open. */
export function buildWorkspaces(src: {
  active?: readonly ActiveMount[];
  subject?: string;
  memberships?: readonly NavMembership[];
}): NavWorkspace[] {
  const active = src.active ?? [];
  const home = active.find((m) => isHomeMount(m, src.subject));
  const desk: NavWorkspace = {
    key: "desk", slug: undefined, kind: "desk",
    name: home?.name?.trim() || "Desk",
  };

  const globalMount = active.find((m) => m.slug === GLOBAL_SLUG);
  // "shown as the company name when known" — known means the mount carries a display name. With no
  // name we say `_global` rather than invent an org: a guessed company name is worse than a slug.
  const global: NavWorkspace | null = globalMount
    ? { key: GLOBAL_SLUG, slug: GLOBAL_SLUG, kind: "global", name: globalMount.name?.trim() || GLOBAL_SLUG }
    : null;

  const groups = new Map<string, NavWorkspace>();
  for (const m of active) {
    if (isHomeMount(m, src.subject) || m.slug === GLOBAL_SLUG || m.slug === SYSTEM_SLUG) continue;
    groups.set(m.slug, { key: m.slug, slug: m.slug, kind: "group", name: m.name?.trim() || m.slug });
  }
  // A membership whose workspace is not mounted right now is still the reader's to open — parking
  // is a focus choice, not a permission (PRD §5.2: the mount set is soft). It wears the name its
  // identity carries; the id is a fallback, never the label we choose.
  for (const ms of src.memberships ?? []) {
    const id = ms.workspace_id;
    if (!id || id === GLOBAL_SLUG || id === SYSTEM_SLUG || groups.has(id)) continue;
    if (home && (id === home.slug || id === src.subject)) continue;
    groups.set(id, { key: id, slug: id, kind: "group", name: ms.name?.trim() || id });
  }

  return [desk, ...(global ? [global] : []), ...[...groups.values()].sort(byName)];
}

// ── the tree ─────────────────────────────────────────────────────────────────────────────────────

export interface TreeNode {
  name: string;
  /** the path from the workspace root — a directory's has no trailing slash */
  path: string;
  dir: boolean;
  children: TreeNode[];
}

/** A flat path list → a tree, machinery dropped. Directories before files, each alphabetical:
 *  the same order the panel's folder listing already uses, so walking one and reading the other
 *  never re-sorts under the reader.
 *
 *  `slug` says WHICH workspace these paths came from, because the hide list is not the same in all
 *  of them: `_global/flows/` is the generated flow pages, which is reading, and a desk's `flows/`
 *  is the agent's own playbooks, which is not. Omitted = the desk's answer. */
export function treeFrom(paths: readonly string[], slug?: string): TreeNode[] {
  const roots: TreeNode[] = [];
  const dirs = new Map<string, TreeNode>();
  for (const path of humanPaths(paths, slug)) {
    const segs = path.split("/").filter(Boolean);
    if (!segs.length) continue;
    let parent: TreeNode[] = roots;
    let here = "";
    for (let i = 0; i < segs.length; i++) {
      here = here ? `${here}/${segs[i]}` : segs[i];
      const leaf = i === segs.length - 1;
      if (leaf) {
        if (!parent.some((n) => !n.dir && n.name === segs[i])) parent.push({ name: segs[i], path: here, dir: false, children: [] });
        break;
      }
      let node = dirs.get(here);
      if (!node) {
        node = { name: segs[i], path: here, dir: true, children: [] };
        dirs.set(here, node);
        parent.push(node);
      }
      parent = node.children;
    }
  }
  const sort = (nodes: TreeNode[]): TreeNode[] => {
    nodes.sort((a, b) => (a.dir === b.dir ? a.name.toLowerCase().localeCompare(b.name.toLowerCase()) : a.dir ? -1 : 1));
    for (const n of nodes) if (n.dir) sort(n.children);
    return nodes;
  };
  return sort(roots);
}

// ── the filter ───────────────────────────────────────────────────────────────────────────────────

/** Enough to answer "where is that file?" without becoming a result page. */
export const MAX_HITS = 50;

export interface ParsedQuery {
  /** what to match — the `>` is not part of it */
  text: string;
  /** the reader asked for CONTENT search (`>` prefix) */
  content: boolean;
}

/** `>` = content, anything else = names (decision 27.3). The prefix is stripped either way, so a
 *  build with no content-search route still answers the question the reader typed. */
export function parseQuery(raw: string): ParsedQuery {
  const s = String(raw ?? "");
  const content = s.trimStart().startsWith(">");
  return { text: (content ? s.trimStart().slice(1) : s).trim(), content };
}

export interface FilterGroup { key: string; name: string; slug?: string; paths: string[] }
export interface FilterResult { groups: FilterGroup[]; shown: number; truncated: boolean }

/** Name match, case-insensitive substring, across every listed workspace, grouped by workspace and
 *  capped — the cap counts HITS, not groups, so one enormous workspace cannot starve the next one
 *  of a row: the groups are filled in list order and the cap stops mid-list, visibly.
 *
 *  Matching is on the file's NAME, which is what the box says it does. A path substring would make
 *  `kg` return a workspace and read as broken to anyone who typed a filename. */
export function filterByName(
  query: string,
  workspaces: readonly NavWorkspace[],
  trees: Readonly<Record<string, readonly string[] | undefined>>,
  limit: number = MAX_HITS,
): FilterResult {
  const { text } = parseQuery(query);
  const needle = text.toLowerCase();
  if (!needle) return { groups: [], shown: 0, truncated: false };
  const groups: FilterGroup[] = [];
  let shown = 0;
  let truncated = false;
  for (const ws of workspaces) {
    const paths = humanPaths(trees[ws.key] ?? [], ws.slug);
    const hits = paths
      .filter((p) => (p.split("/").pop() ?? p).toLowerCase().includes(needle))
      .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    if (!hits.length) continue;
    const room = limit - shown;
    if (room <= 0) { truncated = true; break; }
    if (hits.length > room) truncated = true;
    const take = hits.slice(0, room);
    shown += take.length;
    groups.push({ key: ws.key, name: ws.name, slug: ws.slug, paths: take });
  }
  return { groups, shown, truncated };
}

// ── the browser's memory ─────────────────────────────────────────────────────────────────────────

/** Remembered per browser (decision 27.4), and DEFAULT HIDDEN: absent storage, locked-down storage
 *  and a storage that throws all mean the same thing — the rail is not shown. */
export const NAV_OPEN_KEY = "vexa.minutes.navigator";

export function loadNavOpen(): boolean {
  try { return localStorage.getItem(NAV_OPEN_KEY) === "1"; } catch { return false; }
}
export function saveNavOpen(open: boolean): void {
  try { localStorage.setItem(NAV_OPEN_KEY, open ? "1" : "0"); } catch { /* locked-down storage */ }
}

// ── the fetches (thin: every decision above is pure) ──────────────────────────────────────────────

/** The workspaces this reader is shown, and nothing else — the desk is never read for ids to list.
 *
 *  Each membership is resolved to its identity, which is where both halves of the ruling are paid:
 *  the NAME comes back with it, and `gone` / `not-yours` drop the row. Best-effort at every hop —
 *  a dead membership index, or a lookup that throws, must not cost the reader a workspace they are
 *  in, nor their own desk, which is the one workspace that always exists. A lookup that FAILS is
 *  unknown, not refused: only an answer removes a row. */
export async function loadNavWorkspaces(): Promise<NavWorkspace[]> {
  const [set, memberships] = await Promise.all([
    readActiveSet().catch(() => null),
    listSharedMemberships().catch(() => [] as Membership[]),
  ]);
  const named = await Promise.all((memberships ?? []).map(async (ms): Promise<NavMembership | null> => {
    if (!ms?.workspace_id) return null;
    const who = await readWorkspaceById(ms.workspace_id).catch(() => null);
    if (who && who.access !== "readable") return null;
    return { workspace_id: ms.workspace_id, name: who?.name ?? null };
  }));
  return buildWorkspaces({
    active: set?.active ?? [],
    subject: set?.subject,
    memberships: named.filter((m): m is NavMembership => m !== null),
  });
}

/** One workspace's file list. `slug: undefined` reads the caller's own desk. */
export async function loadNavTree(ws: NavWorkspace): Promise<string[]> {
  return listWorkspaceTree(ws.slug ? { slug: ws.slug } : undefined).catch(() => [] as string[]);
}
