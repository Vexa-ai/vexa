/** docLinks — ONE resolution path for every link format a workspace doc can carry.
 *
 *  The workspace renders three link spellings — [[Wikilink]] titles, workspace paths
 *  (`kg/entities/person/x.md`), and relative markdown links (`../entities/project/dna.md`)
 *  — through two renderers (MdxDoc and the plain-Markdown fallback). Before this module
 *  each site resolved links its own way, always against the user's OWN workspace tree, so
 *  links inside a SHARED workspace's docs silently did nothing. Everything now funnels
 *  through resolveDocRef(), which is:
 *    - slug-aware: searches the doc's OWN workspace first, then every ACTIVE mount in
 *      order, then the legacy no-slug (seed-slot) read strictly last (ADR-0028);
 *    - base-aware: relative paths normalize against the linking doc's directory;
 *    - loud: an unresolvable [[wikilink]] renders as a muted chip with a "not found"
 *      tooltip instead of a click that does nothing.
 */
"use client";
import { createContext, useContext, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";
import { Icon } from "./index";

// ── contexts ─────────────────────────────────────────────────────────────────────
/** `slug` (when the key is PRESENT) pins the target workspace — including `undefined`
 *  meaning the home workspace; when the key is absent the doc's own workspace applies. */
export type DocNavigate = (detail: { path?: string; wikilink?: string; slug?: string }) => void;
/** Obsidian-style in-place navigation: the hosting doc pane provides a navigate fn so
 *  links replace the pane's content (with its own back/forward history). Outside a doc
 *  pane (chat, demo page) links fall back to opening a workbench tab. */
export const DocNavContext = createContext<DocNavigate | null>(null);
/** WHERE the rendering doc lives: its own workspace-relative path (base for relative
 *  links) and its workspace slug (undefined = the user's own workspace). Provided by
 *  the doc pane; empty in chat. */
export const DocMetaContext = createContext<{ path?: string; slug?: string }>({});

export function useOpenEntity(): DocNavigate {
  const nav = useContext(DocNavContext);
  const meta = useContext(DocMetaContext);
  return nav ?? ((detail) => {
    if (typeof window !== "undefined") {
      const slug = "slug" in detail ? detail.slug : meta.slug;
      window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { ...detail, slug, docPath: meta.path } }));
    }
  });
}

// ── path + slug helpers ──────────────────────────────────────────────────────────
export const entitySlug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** Normalize a schemeless href into a workspace-relative path. `./x` and `../x` resolve
 *  against the linking doc's directory; anything else is taken from the workspace root. */
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
  const p = import("../surfaces/workspaceApi")
    .then((api) => api.listWorkspaceTree(slug ? { slug } : undefined))
    .catch(() => [] as string[]);
  treeCache.set(key, { at: Date.now(), p });
  return p;
}
/** The mount table, cached WHOLE rather than as a list of slugs: every mount reports the worker
 *  path it is mounted at, plus the `subject` whose private baseline this client addresses with NO
 *  slug. That table is what turns an absolute path the agent quoted into {workspace, relative} —
 *  read off the server's own answer instead of guessed from the directory layout. */
export interface Mount { slug: string; path: string; primary: boolean }
interface MountedSet { subject?: string; mounts: Mount[] }
const EMPTY_MOUNTS: MountedSet = { mounts: [] };
let mountedCache: { at: number; p: Promise<MountedSet> } | null = null;
function mountedSet(): Promise<MountedSet> {
  if (mountedCache && Date.now() - mountedCache.at < CACHE_TTL_MS) return mountedCache.p;
  const p = import("../surfaces/workspaceApi")
    .then((api) => api.readActiveSet())
    .then((s) => ({
      subject: s.subject,
      mounts: s.active.map((m) => ({ slug: m.slug, path: m.path ?? "", primary: Boolean(m.primary) })),
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
  m.primary || m.slug === subject || (!!subject && m.path.endsWith(`/${subject}`));

/** The workspaces to search, in mount order, with the baseline as `undefined` (a no-slug read). */
function activeSlugs(): Promise<(string | undefined)[]> {
  return mountedSet().then((s) => s.mounts.map((m) => (isHomeMount(m, s.subject) ? undefined : m.slug)));
}

// ── the known-workspace set (a SYNCHRONOUS snapshot, for the transform layer) ─────
// Recognizing a workspace NAME in a reply has to happen while rewriting a string, which cannot
// await. So the mount table is mirrored into a snapshot the transform reads directly, and the
// renderer primes it once and recompiles when it lands. Empty snapshot ⇒ no workspace chips —
// never a guess.
let mountedSnapshot: MountedSet = EMPTY_MOUNTS;
/** The mounted workspaces as last read. Synchronous; empty until primeKnownWorkspaces resolves. */
export function knownWorkspaces(): Mount[] { return mountedSnapshot.mounts; }
/** Fill the snapshot (idempotent, shares the mount-table cache). */
export async function primeKnownWorkspaces(): Promise<void> { mountedSnapshot = await mountedSet(); }

/** `_global` is the org tier every subject reads; `personal` is this client's own LABEL for the
 *  private baseline (slug `undefined` — see PagesPanel's `slug ?? "personal"`). Neither appears in
 *  the mount table under those names, and both are things a reply legitimately names. */
const ORG_TIER = "_global";
const PERSONAL = "personal";

/** Resolve a token a reply used to NAME a workspace → the workspace it means, or undefined.
 *
 *  A CLOSED set: the mounted slugs, plus `_global` and `personal`. Never a fuzzy match on
 *  slug-shaped words — a chip that opens nothing is the defect being fixed here, not a smaller
 *  version of it. (founder, 2026-09-01: "workspace reference must be a link to its readme".) */
export function lookupWorkspace(token: string): { slug?: string; label: string } | undefined {
  const t = token.trim();
  if (!t) return undefined;
  if (t === PERSONAL) return { slug: undefined, label: t };
  if (t === ORG_TIER) return { slug: ORG_TIER, label: t };
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
export interface DocRef { path?: string; wikilink?: string }
export interface DocMeta { path?: string; slug?: string }
export interface ResolvedDoc { path: string; slug?: string; type?: string }

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
  const sibling = !worker && meta.path ? normalizeDocPath(`./${path.replace(/^\.\//, "")}`, meta.path) : null;
  for (const ws of await searchOrder(meta)) {
    const tree = await workspaceTree(ws);
    if (tree.includes(root) || (sibling && tree.includes(sibling))) return true;
  }
  return false;
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
    if (worker) {
      const tree = await workspaceTree(worker.slug);
      if (tree.includes(worker.path)) return { path: worker.path, slug: worker.slug };
      // attached-store slug didn't resolve — try the rest of the search order before giving up
      for (const ws of await searchOrder(meta)) {
        if (ws !== worker.slug && (await workspaceTree(ws)).includes(worker.path)) return { path: worker.path, slug: ws };
      }
      return { path: worker.path, slug: worker.slug };
    }
    // Try root-relative first, then doc-relative (authors write both `kg/x.md` and `entities/x.md`
    // meaning a sibling) — pick whichever actually exists, searching workspaces in order.
    const root = normalizeDocPath(ref.path, meta.path);
    const sibling = meta.path ? normalizeDocPath(`./${ref.path.replace(/^\.\//, "")}`, meta.path) : null;
    const order = await searchOrder(meta);
    for (const ws of order) {
      const tree = await workspaceTree(ws);
      if (tree.includes(root)) return { path: root, slug: ws };
      if (sibling && tree.includes(sibling)) return { path: sibling, slug: ws };
    }
    // Not in any tree — still open it in the most specific workspace (the doc tab shows
    // "(not found)", which is louder and more debuggable than a click that does nothing).
    return { path: root, slug: order[0] };
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

// ── entity chip styling (mirrors the TYPE map in surfaces/entities.tsx) ───────────
export const ENTITY_CHIP: Record<string, { icon: string; color: string; bg: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)" },
  organization: { icon: "web", color: "var(--violet)", bg: "var(--violetbg)" },
  project: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
  meeting: { icon: "cal", color: "var(--violet)", bg: "var(--violetbg)" },
  task: { icon: "tasks", color: "var(--green)", bg: "var(--greenbg)" },
  product: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
};
export const DEFAULT_ENTITY_CHIP = { icon: "link", color: "var(--blue)", bg: "var(--bluebg)" };

/** The ONE entity-kind → color lookup for the whole client (chips, inline transcript
 *  highlights, entity dots). Returns undefined for unknown kinds so each site picks its
 *  own fallback (chips default blue, transcript text defaults muted). */
export function entityColor(kind?: string): string | undefined {
  return kind ? ENTITY_CHIP[kind]?.color : undefined;
}

/** Rich entity chip for [[wikilinks]] — typed pill (icon + color per entity type).
 *  Resolves against the doc's workspace (DocMetaContext); a title that matches no entity
 *  doc renders muted with a "not found" tooltip instead of a dead click. */
export function Wikilink({ title }: { title: string }) {
  const [hover, setHover] = useState(false);
  const meta = useContext(DocMetaContext);
  // undefined = resolving, null = not found, ResolvedDoc = found
  const [target, setTarget] = useState<ResolvedDoc | null | undefined>(undefined);
  useEffect(() => {
    let on = true;
    void resolveDocRef({ wikilink: title }, meta).then((r) => { if (on) setTarget(r ?? null); });
    return () => { on = false; };
  }, [title, meta.path, meta.slug]);
  const openEntity = useOpenEntity();
  const missing = target === null;
  const c = (target?.type && ENTITY_CHIP[target.type]) || DEFAULT_ENTITY_CHIP;
  if (missing) {
    // MUTED, NOT DEAD (founder, 2026-09-01). The dashed chip says honestly that no doc carries this
    // title yet — but it still OPENS, landing on the page's empty state, because a chip that eats
    // its own click is indistinguishable from a broken app. Everything the reader can see says
    // "clickable"; only the handler disagreed.
    return (
      <span role="link" onClick={() => openEntity({ wikilink: title })}
        title={`No doc for “${title}” in the mounted workspaces yet — opens the empty page`}
        onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "baseline",
          background: "var(--panel2)", border: `1px dashed ${hover ? "var(--line2)" : "var(--line)"}`, borderRadius: 999,
          padding: "0.5px 9px 0.5px 7px", color: hover ? "var(--t2)" : "var(--t3)", fontSize: "0.92em",
          fontWeight: 500, cursor: "pointer", whiteSpace: "nowrap", lineHeight: 1.45 }}>
        <Icon name="link" size={11} style={{ opacity: 0.5 }} />
        {title}
      </span>
    );
  }
  return (
    <span onClick={() => { if (target) openEntity({ path: target.path, slug: target.slug }); else openEntity({ wikilink: title }); }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "baseline",
        background: hover ? c.bg : "var(--panel2)",
        border: `1px solid ${hover ? c.color : "var(--line)"}`, borderRadius: 999,
        padding: "0.5px 9px 0.5px 7px", color: c.color, fontSize: "0.92em",
        fontWeight: 500, cursor: "pointer", whiteSpace: "nowrap", lineHeight: 1.45 }}>
      <Icon name={c.icon} size={11} style={{ opacity: 0.8 }} />
      {title}
    </span>
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
  const meta = useContext(DocMetaContext);
  const openEntity = useOpenEntity();
  const [hover, setHover] = useState(false);
  const absolute = WORKER_PATH.test(path);
  const [live, setLive] = useState(absolute);
  useEffect(() => {
    if (absolute) { setLive(true); return; }
    let on = true;
    void docPathExists(path, meta).then((ok) => { if (on) setLive(ok); });
    return () => { on = false; };
  }, [path, absolute, meta.path, meta.slug]);
  const base: CSSProperties = {
    fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)",
    border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px",
  };
  if (!live) return <code style={{ ...base, color: "var(--t1)" }}>{path}</code>;
  return (
    <code role="link" title={`Open ${path}`} onClick={() => openEntity({ path })}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ ...base, borderColor: hover ? "var(--blue)" : "var(--line)", color: "var(--blue)",
        cursor: "pointer", textDecoration: hover ? "underline" : "none" }}>{path}</code>
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
  const [hover, setHover] = useState(false);
  const openEntity = useOpenEntity();
  const ws = lookupWorkspace(token);
  if (!ws) return <>{token}</>;    // the snapshot moved under us — plain text, never a dead chip
  return (
    <span role="link" onClick={() => openEntity({ path: "README.md", slug: ws.slug })}
      title={`Open the ${ws.label} workspace README`}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "baseline",
        background: hover ? "var(--violetbg)" : "var(--panel2)",
        border: `1px solid ${hover ? "var(--violet)" : "var(--line)"}`, borderRadius: 999,
        padding: "0.5px 9px 0.5px 7px", color: "var(--violet)", fontSize: "0.92em",
        fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap", lineHeight: 1.45 }}>
      <Icon name="folder" size={11} style={{ opacity: 0.8 }} />
      {ws.label}
    </span>
  );
}

/** Workspace-internal link (schemeless href) — navigates the doc pane in place (or opens
 *  a tab outside one), resolving relative hrefs against the linking doc. Both renderers
 *  (MdxDoc's `a` mapping and the plain-Markdown fallback) emit this for internal links. */
export function InternalLink({ href, children }: { href: string; children?: ReactNode }) {
  const meta = useContext(DocMetaContext);
  const openEntity = useOpenEntity();
  // absolute = a worker-visible mount path — pass verbatim; resolveDocRef translates it
  const path = href.startsWith("/") ? href : normalizeDocPath(href.replace(/^\.\//, ""), meta.path);
  return (
    <span role="link" onClick={() => openEntity({ path })}
      style={{ color: "var(--blue)", textDecoration: "underline", cursor: "pointer" }}>{children}</span>
  );
}

/** Link-card (Mintlify <Card>) — lives here so BOTH renderers share one implementation:
 *  MdxDoc registers it in the MDX component vocabulary, and the plain-Markdown fallback
 *  reconstructs it from raw <Card> tags so a failed MDX compile doesn't print tag soup. */
export function Card({ title, icon, href, children }: { title?: string; icon?: string; href?: string; children?: ReactNode }) {
  const [hover, setHover] = useState(false);
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
    <div onClick={clickable ? open : undefined} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ border: `1px solid ${hover && clickable ? "var(--line2)" : "var(--line)"}`, borderRadius: 10, background: hover && clickable ? "var(--panel2)" : "var(--panel)", padding: "12px 14px", cursor: clickable ? "pointer" : undefined, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: children ? 6 : 0 }}>
        {icon && <span style={{ color: "var(--blue)" }}><Icon name={icon} size={14} /></span>}
        <span style={{ fontWeight: 600, color: "var(--t1)", fontSize: 13.5 }}>{title}</span>
      </div>
      <div style={{ color: "var(--t2)", fontSize: 13, lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}

export function CardGroup({ cols = 2, children }: { cols?: number; children?: ReactNode }) {
  return <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 10, margin: "8px 0 12px" }}>{children}</div>;
}

/** True when an href points inside the workspace (no scheme, not an anchor, not //host). */
export const isInternalHref = (href?: string): boolean =>
  Boolean(href) && !/^[a-z][a-z0-9+.-]*:/i.test(href!) && !href!.startsWith("#") && !href!.startsWith("//");
