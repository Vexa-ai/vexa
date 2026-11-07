/** docLinks — ONE resolution path for every link format a workspace doc can carry.
 *
 *  The workspace renders three link spellings — [[Wikilink]] titles, workspace paths
 *  (`kg/entities/person/x.md`), and relative markdown links (`../entities/project/dna.md`)
 *  — through two renderers (MdxDoc and the plain-Markdown fallback). Before this module
 *  each site resolved links its own way, always against the user's OWN workspace tree, so
 *  links inside a SHARED workspace's docs silently did nothing. Everything now funnels
 *  through resolveDocRef(), which is:
 *    - QUALIFIED (founder ruling 2026-10-18: "we must require full paths to specific
 *      workspaces, we do not want to guess which readme this should open"): a PATH resolves only
 *      in the workspace it names — a mount path, `[[page:<ws>/<path>]]`, a workspace chip — or, for
 *      a relative link inside a page, in that page's own workspace. A bare path with no context
 *      (`README.md` in a chat reply) resolves to NOTHING and renders as plain, unlinked text. The
 *      search across `_global` and every mount that used to answer it is gone: it is how a chat's
 *      `README.md` opened the organisation's README. [[Title]] entity names still search (#1783);
 *    - base-aware: relative paths normalize against the linking doc's directory;
 *    - loud: an unresolvable [[wikilink]] renders as a muted chip with a "not found"
 *      tooltip instead of a click that does nothing.
 */
"use client";
import { useContext, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "./index";
import { isWsRef } from "./wsLinks";
import { WsLink, isQualifiedRef } from "./WsLink";
import { RefChip } from "./RefChip";
import { parseUniversalRef } from "./universalRefs";
import { usePeekOrOpen } from "./peekSeam";
import { resolveLink } from "./wsLinks";
import { MeetingChip, meetingNotePath, meetingRefOfHref } from "./MeetingChip";
// The link vocabulary both renderers share. Re-exported below so every existing importer of
// docLinks keeps working — the extraction is about the dependency graph, not about the API.
import { DocMetaContext, ENTITY_CHIP, DEFAULT_ENTITY_CHIP, toneOf, useOpenEntity } from "./docRefs";

export { DocNavContext, DocMetaContext, useOpenEntity, ENTITY_CHIP, DEFAULT_ENTITY_CHIP, entityColor } from "./docRefs";
export type { DocNavigate } from "./docRefs";

// ── path + slug helpers ──────────────────────────────────────────────────────────
/** core/workspaces/shared/entities.py `_TRANSLIT`, verbatim (scripts/parity.json `entity-slug-translit`
 *  holds the two to one spelling): lowercase letter:latin, space-separated, a two-letter key a digraph. */
const TRANSLIT = "а:a б:b в:v г:g д:d е:e ж:zh з:z и:i й:i к:k л:l м:m н:n о:o п:p р:r с:s т:t у:u ф:f х:kh ц:ts ч:ch ш:sh щ:shch ъ:ie ы:y ь: э:e ю:iu я:ia ё:e є:ie і:i ї:i ґ:g ў:u ђ:dj ј:j љ:lj њ:nj ћ:c џ:dz ѓ:gj ќ:kj ѕ:dz α:a β:v γ:g δ:d ε:e ζ:z η:i θ:th ι:i κ:k λ:l μ:m ν:n ξ:x ο:o π:p ρ:r σ:s ς:s τ:t υ:y φ:f χ:ch ψ:ps ω:o ου:ou";
const TRANSLIT_MAP = new Map(TRANSLIT.split(" ").map((p) => { const i = p.indexOf(":"); return [p.slice(0, i), p.slice(i + 1)] as const; }));
const translitBase = (ch: string) => (Array.from(ch.normalize("NFKD"))[0] ?? "").toLowerCase();

/** Cyrillic and Greek letters to their Latin spelling; everything else passes through untouched. */
function transliterate(text: string): string {
  const chars = Array.from(text.normalize("NFC"));
  let out = "";
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    if (ch.charCodeAt(0) < 0x80) { out += ch; continue; }
    const pair = i + 1 < chars.length ? translitBase(ch) + translitBase(chars[i + 1]) : "";
    if (pair && TRANSLIT_MAP.has(pair)) { out += TRANSLIT_MAP.get(pair); i++; continue; }
    const t = TRANSLIT_MAP.get(ch.toLowerCase()) ?? TRANSLIT_MAP.get(translitBase(ch));
    out += t ?? ch;
  }
  return out;
}

/** FNV-1a 32 over UTF-8, hex — the server's `_fnv1a32`. */
function fnv1a32(text: string): string {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}

/** The entity id the SERVER gives a name (core/workspaces/shared/entities.py `slugify`), so a chip
 *  finds the page the server wrote: apostrophes dropped, Cyrillic and Greek transliterated, accents
 *  folded (NFKD, combining marks removed), every other non-alphanumeric run one hyphen, at most 80
 *  characters; a name with letters but no Latin spelling (CJK, Arabic, …) is `u-<fnv1a32>`.
 *  Dropping the non-ASCII letter instead of folding it sent `[[mühlemann+popp AG]]` to a page that
 *  never existed. */
export function entitySlug(s: string): string {
  const raw = s.replace(/['’]/g, "");
  const slug = transliterate(raw).normalize("NFKD").replace(/[^\x00-\x7f]/g, "").replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").toLowerCase();
  if (!slug && /[\p{L}\p{N}]/u.test(raw)) return `u-${fnv1a32(raw.normalize("NFC").toLowerCase().trim().split(/\s+/).join(" "))}`;
  return slug.slice(0, 80);
}

/** Normalize a schemeless href into a workspace-relative path. `./x` and `../x` resolve
 *  against the linking doc's directory; anything else is taken from the workspace root. */
/** A relative href that climbs OUT of its workspace names a sibling workspace by its first segment
 *  (#1783): `../../biz/graph/sg/x.md` from `kg/notes/a.md` is `biz:graph/sg/x.md`. `null` when the
 *  link stays inside. */
export function climbedOutRef(href: string, docPath?: string): string | null {
  const clean = href.replace(/[?#].*$/, "");
  if (!/^\.\.?(\/|$)/.test(clean)) return null;
  const parts = [...(docPath ? docPath.split("/").slice(0, -1) : []), ...clean.split("/")];
  const out: string[] = [];
  let above = 0;
  for (const p of parts) {
    if (!p || p === ".") continue;
    if (p === "..") { if (out.length) out.pop(); else if (above === 0) above = 1; else return null; }
    else out.push(p);
  }
  // exactly one level above the root: the first segment is the sibling workspace's slug
  return above === 1 && out.length >= 2 ? `${out[0]}:${out.slice(1).join("/")}` : null;
}

export function normalizeDocPath(href: string, docPath?: string): string {
  const clean = href.replace(/[?#].*$/, "");
  const relative = /^\.\.?(\/|$)/.test(clean);
  const parts = [...(relative && docPath ? docPath.split("/").slice(0, -1) : []), ...clean.split("/")];
  const out: string[] = [];
  for (const p of parts) {
    if (!p || p === ".") continue;
    if (p === "..") out.pop();
    else out.push(p);
  }
  return out.join("/");
}

// ── per-workspace caches (short TTL — agents create entities while docs are open) ──
const CACHE_TTL_MS = 60_000;
const HOME = "";  // map key for "no slug" (the user's own workspace)
const GLOBAL = "_global";  // mandatory first tier of every agent/runtime mount stack
const treeCache = new Map<string, { at: number; p: Promise<string[]> }>();
function workspaceTree(slug?: string): Promise<string[]> {
  const key = slug ?? HOME;
  const hit = treeCache.get(key);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.p;
  const p = import("../data/workspaceApi")
    .then((api) => api.listWorkspaceTree(slug ? { slug } : undefined))
    .catch(() => [] as string[]);
  treeCache.set(key, { at: Date.now(), p });
  return p;
}
/** PLAIN NAMES THAT ARE PAGES (#1783). The entity pages of a workspace, as the names a writer would
 *  type — `kg/entities/person/pat-mason.md` → "Pat Mason" — so a page that mentions Pat Mason in
 *  prose links him without the writer having typed `[[ ]]`. Primed asynchronously; read in sync. */
const entityNames = new Map<string, string[]>();
export function knownEntityNames(slug?: string): string[] { return entityNames.get(slug ?? HOME) ?? []; }
export async function primeEntityNames(slug?: string): Promise<boolean> {
  const tree = await workspaceTree(slug);
  const names = [...new Set(tree.map((p) => /(?:^|\/)kg\/entities\/[^/]+\/([^/]+)\.md$/.exec(p)?.[1]).filter((x): x is string => !!x)
    .map((stem) => stem.split(/[-_]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ")))];
  const before = (entityNames.get(slug ?? HOME) ?? []).join("\u0000");
  entityNames.set(slug ?? HOME, names);
  return before !== names.join("\u0000");
}

/** The mount table, cached WHOLE rather than as a list of slugs: every mount reports the worker
 *  path it is mounted at, plus the `subject` whose private baseline this client addresses with NO
 *  slug. That table is what turns an absolute path the agent quoted into {workspace, relative} —
 *  read off the server's own answer instead of guessed from the directory layout. */
export interface Mount { slug: string; path: string; primary: boolean; desk?: boolean }
interface MountedSet { subject?: string; mounts: Mount[] }
const EMPTY_MOUNTS: MountedSet = { mounts: [] };
let mountedCache: { at: number; p: Promise<MountedSet> } | null = null;
function mountedSet(): Promise<MountedSet> {
  if (mountedCache && Date.now() - mountedCache.at < CACHE_TTL_MS) return mountedCache.p;
  const p = import("../data/workspaceApi")
    .then((api) => api.readActiveSet())
    .then((s) => ({
      subject: s.subject,
      mounts: s.active.map((m) => ({ slug: m.slug, path: m.path ?? "", primary: Boolean(m.primary), ...(m.desk !== undefined ? { desk: m.desk } : {}) })),
    }))
    .catch(() => EMPTY_MOUNTS);
  mountedCache = { at: Date.now(), p };
  return p;
}

/** Is this mount the one this client reads with NO slug — the private baseline in the seed slot?
 *  It does NOT announce itself by name: the server calls it `seed`, so matching on the slug alone
 *  sends every home read to a workspace called "seed" that no slug-addressed endpoint serves. The
 *  authoritative marks are `primary` and the mount path being `<root>/<subject>`. */
const isHomeMount = (m: Mount, subject?: string): boolean =>
  (m.desk ?? m.primary) || m.slug === subject || (!!subject && m.path.endsWith(`/${subject}`));

/** The workspaces to search, in mount order, with the baseline as `undefined` (a no-slug read). */
function activeSlugs(): Promise<(string | undefined)[]> {
  return mountedSet().then((s) => s.mounts.map((m) => (isHomeMount(m, s.subject) ? undefined : m.slug)));
}

/** THE REACHABLE SET — every workspace this reader can OPEN, which is not the set the chat has
 *  in FOCUS.
 *
 *  `readActiveSet` describes FOCUS: what this conversation mounts right now. Membership is a
 *  PERMISSION, and parking a workspace is a focus choice rather than a revocation — the rail
 *  already says exactly this ("a membership whose workspace is not mounted right now is still the
 *  reader's to open … PRD §5.2: the mount set is soft", navigatorApi.buildWorkspaces).
 *
 *  Resolving a NAMED workspace against the FOCUS set is the defect (founder, 2026-09-05:
 *  "workspace links can open wrong workspaces — we have unique workspace but it does not work"): a
 *  workspace the reader belongs to but has not focused was simply absent from the table, so its
 *  name either chipped nothing or fell through to whatever happened to be mounted.
 *
 *  NAMES resolve here; READS still resolve against the mounts (`activeSlugs` → `searchOrder`), and
 *  that split is deliberate. A membership says a workspace exists and is ours — never where it is
 *  mounted, nor what is inside it — so putting memberships in the search order would buy one tree
 *  fetch per membership for every link in every reply, to answer a question nobody asked. */
let reachableCache: { at: number; p: Promise<MountedSet> } | null = null;
function reachableSet(): Promise<MountedSet> {
  if (reachableCache && Date.now() - reachableCache.at < CACHE_TTL_MS) return reachableCache.p;
  const p = (async (): Promise<MountedSet> => {
    const mounted = await mountedSet();
    const memberships = await import("../data/workspaceApi")
      .then((api) => api.listSharedMemberships())
      .catch(() => [] as { workspace_id: string }[]);
    const mounts = [...mounted.mounts];
    for (const ms of memberships) {
      const slug = (ms.workspace_id ?? "").trim();
      // `path: ""` is the honest value — a membership names a workspace, it does not say where the
      // worker mounted it. Everything that SPLITS a worker path skips path-less rows already.
      if (slug && !mounts.some((m) => m.slug === slug)) mounts.push({ slug, path: "", primary: false });
    }
    return { subject: mounted.subject, mounts };
  })();
  reachableCache = { at: Date.now(), p };
  return p;
}

/** Does this path NAME a workspace this reader can reach?
 *
 *  `undefined` is not a name: it is the home mount, or an unknown root whose `kg/` tail was all we
 *  could address. Neither claims a room, so neither forecloses the search. A slug we cannot PLACE
 *  (a legacy `.attached/<dir>/` storage name, a path quoted from a session whose mounts we never
 *  saw) does not claim one either — there is no such room to open, so the best available answer is
 *  a copy we can actually serve. Only a REACHABLE slug is a claim, and a claim is honoured. */
async function namesReachableWorkspace(slug: string | undefined): Promise<boolean> {
  if (slug === undefined) return false;
  if (slug === GLOBAL) return true;
  const { mounts } = await reachableSet();
  return mounts.some((m) => m.slug === slug);
}

// ── the known-workspace set (a SYNCHRONOUS snapshot, for the transform layer) ─────
// Recognizing a workspace NAME in a reply has to happen while rewriting a string, which cannot
// await. So the reachable set is mirrored into a snapshot the transform reads directly, and the
// renderer primes it once and recompiles when it lands. Empty snapshot ⇒ no workspace chips —
// never a guess.
let mountedSnapshot: MountedSet = EMPTY_MOUNTS;
/** The workspaces this reader can NAME, as last read. Synchronous; empty until
 *  primeKnownWorkspaces resolves. */
export function knownWorkspaces(): Mount[] { return mountedSnapshot.mounts; }
/** Fill the snapshot (idempotent, shares the caches). */
export async function primeKnownWorkspaces(): Promise<void> { mountedSnapshot = await reachableSet(); }

/** `_global` is the org tier every subject reads; `personal` is this client's own LABEL for the
 *  private baseline (slug `undefined` — see PagesPanel's `slug ?? "personal"`). Neither appears in
 *  the mount table under those names, and both are things a reply legitimately names. */
const ORG_TIER = "_global";
const PERSONAL = "personal";

/** The private baseline answers to several NAMES — `personal` (this client's label), `seed` (the
 *  server's slug for the slot) and the subject uid — and not one of them IDENTIFIES it. The PRIMARY
 *  MOUNT does. Keying on a name is how a shared workspace legitimately called `seed` would swallow
 *  every home read, which is the trap `isHomeMount` already documents one level down. */
function isHomeAlias(t: string): boolean {
  if (t === PERSONAL) return true;
  const { mounts, subject } = mountedSnapshot;
  if (subject && t === subject) return true;
  const home = mounts.find((m) => isHomeMount(m, subject));
  return !!home && t === home.slug;
}

/** Resolve a token a reply used to NAME a workspace → the workspace it means, or undefined.
 *
 *  A CLOSED set: every workspace this reader can REACH (mounted or merely a member of — see
 *  `reachableSet`), plus `_global` and `personal`. Never a fuzzy match on slug-shaped words — a
 *  chip that opens nothing is the defect being fixed here, not a smaller version of it. (founder,
 *  2026-09-01: "workspace reference must be a link to its readme".)
 *
 *  EXACT, never a prefix and never a fallback: `vexa-team-3183d1` and `vexa-team-a2a023` are two
 *  different rooms, so a token that half-matches must chip NOTHING rather than the other one, and
 *  an unknown token stays plain prose. Silence is the only safe wrong answer here — the alternative
 *  is a link that opens somebody else's workspace and looks like it worked. */
export function lookupWorkspace(token: string): { slug?: string; label: string } | undefined {
  const t = token.trim();
  if (!t) return undefined;
  if (t === ORG_TIER) return { slug: ORG_TIER, label: t };
  if (isHomeAlias(t)) return { slug: undefined, label: t };
  const hit = mountedSnapshot.mounts.find((m) => m.slug === t);
  if (!hit) return undefined;
  return { slug: isHomeMount(hit, mountedSnapshot.subject) ? undefined : hit.slug, label: t };
}

/** Is this token distinctive enough to chip when it appears as BARE prose (no bold, no backticks)?
 *  Slugs carry a `-` or `_` by construction, so requiring one keeps ordinary English out: a reply
 *  saying "personal notes" must not sprout a workspace chip. Emphasised mentions (bold or inline
 *  code) are deliberate and skip this test. */
export const isDistinctiveWorkspaceToken = (t: string): boolean => /[-_]/.test(t);
/** Drop the caches (e.g. right after activating/attaching a workspace, or when a chat turn commits)
 *  so resolution sees it. */
export function invalidateDocLinkCaches(): void {
  treeCache.clear();
  mountedCache = null;
  reachableCache = null;
  lastMissRefresh = 0;
}

/** A wikilink that misses is USUALLY a stale tree, not a missing entity: the agent WRITES the entity
 *  doc during the very turn whose reply names it, and these caches can be a minute old by then — so
 *  every chip in that reply rendered "not found", and a not-found chip used to be unclickable. Drop
 *  the caches once and look again before calling a title unresolvable. Throttled, because a reply
 *  that names five new entities must not refetch every workspace tree five times. */
let lastMissRefresh = 0;
const MISS_REFRESH_MS = 5_000;
function refreshOnMiss(): boolean {
  if (Date.now() - lastMissRefresh < MISS_REFRESH_MS) return false;
  invalidateDocLinkCaches();          // resets lastMissRefresh — so stamp it after
  lastMissRefresh = Date.now();
  return true;
}

// ── the resolver ──────────────────────────────────────────────────────────────────
/** `slug` + `exact` travel with a ref that NAMES its workspace (the workspace chip). Everything
 *  else leaves the workspace to `meta`, where it is a search ORDER rather than a destination. */
export interface DocRef { path?: string; wikilink?: string; slug?: string; exact?: boolean }
export interface DocMeta { path?: string; slug?: string }
export interface ResolvedDoc { path: string; slug?: string; type?: string }

/** IS THERE A WORKSPACE THIS TEXT WAS WRITTEN IN? Inside a page there is — the page's own (its host
 *  provides `{path, slug}`, `slug` undefined being the home workspace). In a chat there is none:
 *  `DocMetaContext` is empty, and a bare path there names no workspace at all. */
export function hasDocHome(meta: DocMeta): boolean {
  return "slug" in meta || !!meta.path;
}

/** A PATH THAT NAMES NO WORKSPACE (founder ruling 2026-10-18): relative (not a mount path), in a
 *  context with no workspace of its own. It is never resolved by searching — it renders plain. */
export function isUnqualifiedPath(path: string, meta: DocMeta): boolean {
  return !path.startsWith("/") && !hasDocHome(meta);
}

/** `isUnqualifiedPath` against the rendering doc's own context (hook form, for the renderers). */
export function useIsUnqualified(path: string): boolean {
  return isUnqualifiedPath(path, useContext(DocMetaContext));
}

/** The doc context an open-entity event CARRIES — only the keys it really has. `{slug: undefined}`
 *  means the home workspace was named; no `slug` key means none was (a chat), and a resolver handed
 *  `{slug: d.slug}` for the second would read it as the first. */
export function metaOfOpen(d: { docPath?: string; slug?: string }): DocMeta {
  return { ...(d.docPath ? { path: d.docPath } : {}), ...("slug" in d ? { slug: d.slug } : {}) };
}

/** Is this [[wikilink]] text a PATH rather than an entity's name (`README.md`, `kg/x.md`)? */
export const isPathShaped = (t: string): boolean => /\//.test(t) || /\.(?:md|markdown|mdx)$/i.test(t.trim());

/** A worker-visible ABSOLUTE mount path, as the agent quotes it in chat (engine.py
 *  mounts_preamble: "Always use ABSOLUTE paths"). Three layouts reach the client:
 *    `<root>/<subject>/<rel>`                   the private baseline    → home (no slug)
 *    `<root>/<slug>/<rel>`                      a shared / extra mount  → that slug
 *    `<root>/.attached/<subject>/<slug>/<rel>`  the legacy attached one → that slug
 *  Used by the transform layer to decide what becomes a chip, and by fromWorkerPath to split it. */
export const WORKER_PATH = /^\/(?:[\w.-]+\/)*?workspaces\/[\w.-]+\/[\w./ -]+$/;

/** Map a WORKER-VISIBLE absolute path to a workspace ref.
 *
 *  ANY file under a mount is addressable — not only `kg/` ones. Restricting the home mount to a
 *  `kg/` tail is what made the founder's `/workspaces/vexa-team-3183d1/README.md` resolve to
 *  nothing: the README is the workspace's dashboard and sits at the mount root, so the one path
 *  the agent quotes most often was the one shape this could not translate. */
async function fromWorkerPath(p: string): Promise<{ path: string; slug?: string } | undefined> {
  if (!p.startsWith("/")) return undefined;
  const att = p.match(/\/\.attached\/[^/]+\/([^/]+)\/(.+)$/);
  if (att) return { slug: att[1], path: att[2] };
  // The ACTIVE SET is authoritative: every mount reports the worker path it is mounted at, so the
  // split is READ off the server's table rather than inferred. Longest prefix wins (a mount can
  // nest inside another's directory).
  const { subject, mounts } = await mountedSet();
  for (const m of [...mounts].sort((a, b) => b.path.length - a.path.length)) {
    if (m.path && p.startsWith(`${m.path}/`)) {
      return { slug: isHomeMount(m, subject) ? undefined : m.slug, path: p.slice(m.path.length + 1) };
    }
  }
  // Not in the table (mounted since the cache was filled, or a path from another session): fall
  // back to the LAYOUT — `<root>/workspaces/<workspace-dir>/<rel>`. A dot-prefixed segment is
  // platform plumbing (`.system`, `.attached`), never a workspace, so it is not a slug candidate.
  const mount = p.match(/^\/(?:[\w.-]+\/)*?workspaces\/([\w-][\w.-]*)\/(.+)$/);
  if (mount) return { slug: mount[1] === subject ? undefined : mount[1], path: mount[2] };
  const kg = p.match(/\/(kg\/.+)$/);  // unknown root — only the kg/ tail is workspace-addressable
  if (kg) return { path: kg[1] };
  return undefined;
}

const wikilinkMatcher = (title: string) => {
  const slug = entitySlug(title);
  return new RegExp(`(?:^|/)kg/entities/([^/]+)/${slug.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.md$`);
};

/** Search order (ADR-0028: reads are slug-addressed; the ACTIVE SET is the source of truth):
 *  the doc's own workspace, then the MANDATORY organisation tier, then every normal active mount
 *  in order, then the legacy no-slug read (= the seed-slot storage dir) strictly last — it can hold
 *  a DEACTIVATED workspace's tree.
 *
 *  `_global` is added HERE rather than read from the server: GET /workspace/active intentionally
 *  describes only the mutable middle tier, so this mirrors dispatch.build_mount_set()'s real
 *  `[_global, *active, _system]` stack. Without it an organisation entity resolves to nothing. */
async function searchOrder(meta: DocMeta): Promise<(string | undefined)[]> {
  return [...new Set<string | undefined>([
    ...(meta.slug !== undefined ? [meta.slug] : []),
    GLOBAL,
    ...(await activeSlugs()),
    undefined,
  ])];
}

/** Does this path name a doc that ACTUALLY EXISTS in some mounted workspace?
 *
 *  The gate on turning a BARE relative path in inline code (`kg/entities/company/x.md`) into a
 *  chip. resolveDocRef deliberately never fails a path — it opens the "(not found)" tab, which is
 *  the right answer for something the reader deliberately clicked. It is the wrong answer for a
 *  guess made by a regex: `package.json` must stay plain monospace, not become a chip that lands
 *  on an empty page. Cheap — it reads the same cached trees resolveDocRef does. */
export async function docPathExists(path: string, meta: DocMeta = {}): Promise<boolean> {
  const worker = await fromWorkerPath(path);
  const root = worker ? worker.path : normalizeDocPath(path, meta.path);
  // Entity SHAPES are not documents anyone links to: `kg/templates/` holds skeletons, and a
  // skeleton must never become a live chip in a reply.
  if (/(?:^|\/)kg\/templates\//.test(root)) return false;
  // ONE WORKSPACE IS ASKED, the one the path names (a mount path) or the page it sits in — never a
  // search: a bare `README.md` is in every workspace, so "found somewhere" proves nothing.
  if (worker) return (await workspaceTree(worker.slug)).includes(worker.path);
  if (!hasDocHome(meta)) return false;
  const sibling = meta.path ? normalizeDocPath(`./${path.replace(/^\.\//, "")}`, meta.path) : null;
  const tree = await workspaceTree(meta.slug);
  return tree.includes(root) || (!!sibling && tree.includes(sibling));
}

/** Resolve any doc link to a concrete { path, slug } target, or undefined when a
 *  [[wikilink]] matches no entity doc in any mounted workspace. */
export async function resolveDocRef(ref: DocRef, meta: DocMeta = {}): Promise<ResolvedDoc | undefined> {
  if (ref.path) {
    // A worker-visible ABSOLUTE path (quoted verbatim by the agent in chat) carries its own
    // workspace: translate to {slug, relative} and verify against THAT workspace's tree.
    const worker = await fromWorkerPath(ref.path);
    // Entity SHAPES resolve to NOTHING — the same guard docPathExists carries, applied here too.
    // It cannot live in docPathExists alone: the worker-absolute branch just below returns
    // { path, slug } unconditionally, so `/workspaces/<slug>/kg/templates/person.md` quoted by an
    // agent opened a live tab on a skeleton and it read like a record.
    if (/(?:^|\/)kg\/templates\//.test(worker ? worker.path : normalizeDocPath(ref.path, meta.path))) return undefined;
    // AN EXACT REF IS ANSWERED BY THE WORKSPACE IT NAMES, and by no other. The workspace chip is
    // the only thing that sets it, and its target is `README.md` — a filename every workspace has,
    // so ANY search would find a stranger's copy first and open it as though it were the one asked
    // for. Not verifying is the point: an unwritten README opens its empty state in the room the
    // reader named, which is the same contract every other chip keeps.
    if (ref.exact) {
      const target = worker ?? { path: normalizeDocPath(ref.path, meta.path), slug: ref.slug };
      return { path: target.path, slug: target.slug };
    }
    if (worker) {
      const tree = await workspaceTree(worker.slug);
      if (tree.includes(worker.path)) return { path: worker.path, slug: worker.slug };
      // A NAMED WORKSPACE ANSWERS FOR ITSELF, whether or not it holds the file. The search below
      // used to run here unconditionally, and that is the mechanism of the founder's bug: with
      // `/workspaces/<a-workspace-not-in-focus>/README.md` the named tree came back empty (not
      // mounted, so the read 403s), the search ran, and the first ACTIVE mount holding a
      // `README.md` won — a link to one workspace opening another, silently and plausibly.
      // Landing on the honest empty state in the RIGHT room beats landing on the wrong room's real
      // document, and there is no third option: every workspace has a README.
      // …AND SO DOES EVERY OTHER MOUNT PATH (2026-10-18). The search that used to follow here, for
      // a slug claiming no reachable room (home, a legacy `.attached/<dir>/` name), was the same
      // guess one step removed: it took the first other workspace holding a file of that name.
      return { path: worker.path, slug: worker.slug };
    }
    // A BARE PATH IS READ IN THE PAGE IT SITS IN, OR NOWHERE (founder ruling 2026-10-18). With no
    // page around it (a chat reply) it names no workspace: undefined, never a search — the search
    // over `_global` and every mount is how `README.md` in a Narrative chat opened the company's.
    if (!hasDocHome(meta)) return undefined;
    // Root-relative first, then doc-relative (authors write both `kg/x.md` and `entities/x.md`
    // meaning a sibling) — in THIS page's workspace only.
    const root = normalizeDocPath(ref.path, meta.path);
    const sibling = meta.path ? normalizeDocPath(`./${ref.path.replace(/^\.\//, "")}`, meta.path) : null;
    const tree = await workspaceTree(meta.slug);
    if (tree.includes(root)) return { path: root, slug: meta.slug };
    if (sibling && tree.includes(sibling)) return { path: sibling, slug: meta.slug };
    // Not there — still open it in the page's workspace (the doc tab shows "(not found)", which is
    // louder and more debuggable than a click that does nothing).
    return { path: root, slug: meta.slug };
  }
  if (ref.wikilink) {
    const re = wikilinkMatcher(ref.wikilink);
    const look = async (): Promise<ResolvedDoc | undefined> => {
      for (const ws of await searchOrder(meta)) {
        const hit = (await workspaceTree(ws)).find((p) => re.test(p));
        if (hit) return { path: hit, slug: ws, type: re.exec(hit)?.[1] };
      }
      return undefined;
    };
    // A miss is far likelier to be a stale tree than a missing entity (see refreshOnMiss) — so
    // never declare a title unresolvable on cached data alone.
    return (await look()) ?? (refreshOnMiss() ? await look() : undefined);
  }
  return undefined;
}

/** `[[Title]]` asked of the server (`POST /api/links/resolve`, batched) — readable and present, or
 *  undefined so the caller falls back to the tree search. */
async function serverWikilink(title: string, slug?: string): Promise<ResolvedDoc | undefined> {
  try {
    const { resolveLink } = await import("./wsLinks");
    const r = await resolveLink(title, slug);
    if (r.access !== "readable" || r.missing || !r.path) return undefined;
    return { path: r.path, slug: r.slug ?? slug, type: /kg\/entities\/([^/]+)\//.exec(r.path)?.[1] };
  } catch { return undefined; }
}

/** Wikilink — the branch point between the TWO link grammars (PRD decision 26.2).
 *
 *  `[[Title]]` resolves in-workspace, by searching the mounted trees — unchanged, below.
 *  `[[ws:<workspace-id>/<target>]]` resolves on the SERVER, because only the server can tell
 *  "exists and is not yours" from "gone", and the client must render those differently.
 *
 *  A branch and not an `if` inside one component, because the two hold different async state and a
 *  conditional early return above a hook is a rules-of-hooks bug waiting for the day a title
 *  changes under a mounted chip. */
export function Wikilink({ title }: { title: string }) {
  const meta = useContext(DocMetaContext);
  // A UNIVERSAL REFERENCE (`[[room:<id>]]`, `[[chat:<id>|…]]`, `[[page:<ws>/<path>]]`, … #1783): the
  // server says what it is for this reader, with no access check left to the client. It is asked
  // FIRST: `room:s-…` also has the shape of a qualified `<slug>:<target>`, and read that way it
  // resolved as a page in a workspace called "room" and drew as missing text (deploy-57, the
  // `[[room:…]]` that call_person writes).
  if (parseUniversalRef(`[[${title}]]`)) return <RefChip text={`[[${title}]]`} />;
  if (isWsRef(title) || isQualifiedRef(title)) return <WsLink refText={title} slug={meta.slug} />;
  // `[[README.md]]` in a chat names no workspace: plain, never resolved by a search (2026-10-18)
  if (isPathShaped(title) && !hasDocHome(meta)) return <UnqualifiedPath text={title} />;
  return <EntityWikilink title={title} />;
}

/** A PATH THAT NAMES NO WORKSPACE, shown as what it is: text, not a link (founder ruling
 *  2026-10-18). Muted and dotted, with a tooltip saying why it does not open — never a click that
 *  lands in whichever workspace happens to hold a file of that name. */
export function UnqualifiedPath({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <code className="vx-code" data-unqualified="" title={`${text} — this names no workspace, so it is not linked`}>
      {children ?? text}
    </code>
  );
}

/** Rich entity chip for in-workspace [[wikilinks]] — typed pill (icon + color per entity type).
 *  Resolves against the doc's workspace (DocMetaContext); a title that matches no entity
 *  doc renders muted with a "not found" tooltip instead of a dead click. */
function EntityWikilink({ title }: { title: string }) {
  const meta = useContext(DocMetaContext);
  // undefined = resolving, null = not found, ResolvedDoc = found
  const [target, setTarget] = useState<ResolvedDoc | null | undefined>(undefined);
  // NOT HERE → MAYBE ELSEWHERE (#1783): a name with no page in the mounted trees is asked of the
  // server, which looks in every workspace this reader can read (and names none they cannot).
  const [far, setFar] = useState(false);
  useEffect(() => {
    let on = true;
    setFar(false);
    // THE SERVER FIRST (#1783): it holds the one slug rule and the access decision, so a name with
    // accents finds the page the server wrote. The tree search across every mounted workspace stays
    // as the fallback; a name found only in a workspace not mounted here is drawn as reachable.
    void serverWikilink(title, meta.slug)
      .then((hit) => hit ?? resolveDocRef({ wikilink: title }, meta))
      .then((r) => {
        if (!on) return;
        setTarget(r ?? null);
        if (!r) void resolveLink(title, meta.slug).then((x) => {
          if (on && x.access === "readable" && !x.missing && (x.path || x.ambiguous)) setFar(true);
        });
      });
    return () => { on = false; };
  }, [title, meta.path, meta.slug]);
  const openEntity = useOpenEntity();
  const missing = target === null;
  const c = (target?.type && ENTITY_CHIP[target.type]) || DEFAULT_ENTITY_CHIP;
  const peek = usePeekOrOpen(target ? { path: target.path, slug: target.slug, title, kind: target.type } : null);
  if (missing && far) return <WsLink refText={title} slug={meta.slug} label={title} />;
  if (missing) {
    // A PAGE THAT DOES NOT EXIST IS PLAIN TEXT (founder, live 2026-10-15: "pages do not exist, so
    // what's the point?") — no chip, no picker, no empty page behind a click. The agent still
    // creates the page when asked.
    return <span className="vx-ws-missing" data-missing="" data-ws-missing="">{title}</span>;
  }
  // A [[wikilink]] that lands on a meeting's NOTES is the meeting: its title and date, not the
  // page's slug (founder, 2026-10-11: "a special label for a meeting alongside people companies").
  if (target && meetingNotePath(target.path)) return <MeetingChip path={target.path} slug={target.slug} label={title} />;
  // THE ENTITY CHIP (guidelines §4.5): one shape for every reference — radius 6, a border, the
  // type ICON in the type's colour; no colour fill per type and no pill.
  const open = () => { if (target) peek.onActivate(); else openEntity({ wikilink: title }); };
  return (
    <>
      <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind={target?.type ?? "doc"} {...peek.bind}
        onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
        <span className="vx-chip-icon" aria-hidden data-tone={toneOf(c.color)}><Icon name={c.icon} size={12} /></span>
        <span className="vx-chip-label">{title}</span>
      </span>
      {peek.peek}
    </>
  );
}

/** DocPath — the [[wikilink]] chip's twin for FILES: a doc path the agent named in prose or in
 *  inline code, rendered as monospace that OPENS the doc.
 *
 *  The founder asked the agent to "reference workspace with its readme"; the reply printed the
 *  workspace in bold and `/workspaces/vexa-team-3183d1/README.md` as inline code, and neither was
 *  clickable — "no reference, and when reference it's not interactive". CLAUDE.md has promised the
 *  agent for months that "every backticked path becomes a link that opens the doc"; only the
 *  plain-markdown FALLBACK renderer kept that promise. This is the primary renderer keeping it.
 *
 *  Two spellings, two confidence levels:
 *   - an ABSOLUTE mount path is unambiguous — always live. If it resolves to nothing it still
 *     opens, landing on the honest empty state, exactly as a missing [[wikilink]] does;
 *   - a BARE workspace-relative path is a GUESS made by a regex, so it must earn its chip: it goes
 *     live only once it is found in a mounted tree. A miss stays plain monospace, which is why
 *     `package.json` in a reply never becomes a chip that lands nowhere. */
export function DocPath({ path }: { path: string }) {
  // A meeting's notes page is named as the MEETING — never as its filename.
  if (meetingNotePath(path)) return <MeetingChip path={path} />;
  return <DocPathLink path={path} />;
}

function DocPathLink({ path }: { path: string }) {
  const meta = useContext(DocMetaContext);
  const openEntity = useOpenEntity();
  const absolute = WORKER_PATH.test(path);
  const [live, setLive] = useState(absolute);
  const unqualified = !absolute && isUnqualifiedPath(path, meta);
  useEffect(() => {
    if (unqualified) { setLive(false); return; }
    if (absolute) { setLive(true); return; }
    let on = true;
    void docPathExists(path, meta).then((ok) => { if (on) setLive(ok); });
    return () => { on = false; };
  }, [path, absolute, unqualified, meta.path, meta.slug]);
  if (unqualified) return <UnqualifiedPath text={path} />;
  if (!live) return <code className="vx-code">{path}</code>;
  // a LIVE path is a reference you can open: the entity chip, showing the file's name (the full
  // path is the tooltip) — not blue monospace that reads as code (guidelines §4.5)
  const name = path.split("/").filter(Boolean).pop() ?? path;
  return (
    <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind="doc" title={`Open ${path}`}
      onClick={() => openEntity({ path })} onKeyDown={(e) => { if (e.key === "Enter") openEntity({ path }); }}>
      <span className="vx-chip-icon" aria-hidden><Icon name="file" size={12} /></span>
      <span className="vx-chip-label">{name}</span>
    </span>
  );
}

/** WorkspaceRef — a workspace NAMED in a reply, rendered as a chip that opens its README.
 *
 *  The founder's reply said "you already have a shared team workspace mounted — **vexa-team-3183d1**"
 *  in plain bold: the one noun the whole sentence was about, and it went nowhere. His rule:
 *  "workspace reference must be a link to its readme." A workspace's README is its dashboard (see
 *  the seed CLAUDE.md § The README is this workspace's dashboard), so that is the door this opens.
 *  An unwritten README still opens — the honest empty state, same contract as every other chip. */
export function WorkspaceRef({ token }: { token: string }) {
  const openEntity = useOpenEntity();
  const ws = lookupWorkspace(token);
  if (!ws) return <>{token}</>;    // the snapshot moved under us — plain text, never a dead chip
  const open = () => openEntity({ path: "README.md", slug: ws.slug, exact: true });
  return (
    <span role="link" tabIndex={0} className="vx-chip vx-entity vx-inline-chip" data-kind="workspace"
      onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}
      title={`Open the ${ws.label} workspace README`}>
      <span className="vx-chip-icon" aria-hidden><Icon name="folder" size={12} /></span>
      <span className="vx-chip-label">{ws.label}</span>
    </span>
  );
}

/** Workspace-internal link (schemeless href) — navigates the doc pane in place (or opens
 *  a tab outside one), resolving relative hrefs against the linking doc. Both renderers
 *  (MdxDoc's `a` mapping and the plain-Markdown fallback) emit this for internal links. */
export function InternalLink({ href, children }: { href: string; children?: ReactNode }) {
  // `/?meeting=<ref>` and a link to a meeting's notes page are both the meeting (MeetingChip).
  const meetingRef = meetingRefOfHref(href);
  if (meetingRef) return <MeetingChip meetingRef={meetingRef} label={children} />;
  if (meetingNotePath(href)) return <MeetingChip path={href} label={children} />;
  return <PathLink href={href}>{children}</PathLink>;
}

function PathLink({ href, children }: { href: string; children?: ReactNode }) {
  const meta = useContext(DocMetaContext);
  const out = href.startsWith("/") ? null : climbedOutRef(href, meta.path);
  if (out) return <WsLink refText={out} slug={meta.slug} label={children} />;
  return <LocalPathLink href={href}>{children}</LocalPathLink>;
}

function LocalPathLink({ href, children }: { href: string; children?: ReactNode }) {
  const meta = useContext(DocMetaContext);
  const openEntity = useOpenEntity();
  // `[x](README.md)` in a chat names no workspace: its text, unlinked (2026-10-18)
  if (isUnqualifiedPath(href, meta)) return <span className="vx-unqualified" data-unqualified="" title={`${href} — this names no workspace, so it is not linked`}>{children}</span>;
  // absolute = a worker-visible mount path — pass verbatim; resolveDocRef translates it
  const path = href.startsWith("/") ? href : normalizeDocPath(href.replace(/^\.\//, ""), meta.path);
  return (
    <span role="link" onClick={() => openEntity({ path })}
      className="c-info vx-doc-link">{children}</span>
  );
}

/** Link-card (Mintlify <Card>) — lives here so BOTH renderers share one implementation:
 *  MdxDoc registers it in the MDX component vocabulary, and the plain-Markdown fallback
 *  reconstructs it from raw <Card> tags so a failed MDX compile doesn't print tag soup. */
export function Card({ title, icon, href, children }: { title?: string; icon?: string; href?: string; children?: ReactNode }) {
  const clickable = Boolean(href);
  const openEntity = useOpenEntity();
  const meta = useContext(DocMetaContext);
  const open = () => {
    if (!href) return;
    // scheme allowlist: http(s) opens externally, scheme-less opens in-workspace,
    // anything else (javascript:, data:, //host) is untrusted-doc content — ignore
    if (/^https?:/i.test(href)) window.open(href, "_blank", "noreferrer");
    else if (isInternalHref(href)) openEntity({ path: href.startsWith("/") ? href : normalizeDocPath(href.replace(/^\.\//, ""), meta.path) });
  };
  return (
    <div onClick={clickable ? open : undefined} data-clickable={clickable ? "" : undefined}
      className="r-lg pt-3 pr-3 pb-3 pl-3 vx-doc-card">
      <div className="vx-doc-card-head" data-body={children ? "" : undefined}>
        {icon && <span className="c-info"><Icon name={icon} size={14} /></span>}
        <span className="fw-600 c-1 t-sm">{title}</span>
      </div>
      <div className="c-2 t-sm lh-snug">{children}</div>
    </div>
  );
}

/** A card grid's column count as the stylesheet names it (`.vx-doc-cards[data-cols]`, 1–6). */
export function cardColumns(cols: unknown): number {
  const n = Math.round(Number(cols));
  return Number.isFinite(n) ? Math.min(6, Math.max(1, n)) : 2;
}

export function CardGroup({ cols = 2, children }: { cols?: number; children?: ReactNode }) {
  return <div className="mt-2 mr-0 mb-3 ml-0 vx-doc-cards" data-cols={cardColumns(cols)}>{children}</div>;
}

/** True when an href points inside the workspace (no scheme, not an anchor, not //host). */
export const isInternalHref = (href?: string): boolean =>
  Boolean(href) && !/^[a-z][a-z0-9+.-]*:/i.test(href!) && !href!.startsWith("#") && !href!.startsWith("//");
