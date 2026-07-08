/** docLinks — ONE resolution path for every link format a workspace doc can carry.
 *
 *  The workspace renders three link spellings — [[Wikilink]] titles, workspace paths
 *  (`kg/entities/person/x.md`), and relative markdown links (`../entities/project/dna.md`)
 *  — through two renderers (MdxDoc and the plain-Markdown fallback). Before this module
 *  each site resolved links its own way, always against the user's OWN workspace tree, so
 *  links inside a SHARED workspace's docs silently did nothing. Everything now funnels
 *  through resolveDocRef(), which is:
 *    - slug-aware: searches the doc's OWN workspace first, then the home workspace, then
 *      the rest of the mounted active set;
 *    - base-aware: relative paths normalize against the linking doc's directory;
 *    - loud: an unresolvable [[wikilink]] renders as a muted chip with a "not found"
 *      tooltip instead of a click that does nothing.
 */
"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
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
let activeSlugsCache: { at: number; p: Promise<string[]> } | null = null;
function activeSlugs(): Promise<string[]> {
  if (activeSlugsCache && Date.now() - activeSlugsCache.at < CACHE_TTL_MS) return activeSlugsCache.p;
  const p = import("../surfaces/workspaceApi")
    .then((api) => api.readActiveSet())
    .then((s) => s.active.map((m) => m.slug))
    .catch(() => [] as string[]);
  activeSlugsCache = { at: Date.now(), p };
  return p;
}
/** Drop the caches (e.g. right after activating/attaching a workspace) so resolution sees it. */
export function invalidateDocLinkCaches(): void {
  treeCache.clear();
  activeSlugsCache = null;
}

// ── the resolver ──────────────────────────────────────────────────────────────────
export interface DocRef { path?: string; wikilink?: string }
export interface DocMeta { path?: string; slug?: string }
export interface ResolvedDoc { path: string; slug?: string; type?: string }

const wikilinkMatcher = (title: string) => {
  const slug = entitySlug(title);
  return new RegExp(`(?:^|/)kg/entities/([^/]+)/${slug.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.md$`);
};

/** Resolve any doc link to a concrete { path, slug } target, or undefined when a
 *  [[wikilink]] matches no entity doc in any mounted workspace. */
export async function resolveDocRef(ref: DocRef, meta: DocMeta = {}): Promise<ResolvedDoc | undefined> {
  if (ref.path) {
    // Try root-relative first, then doc-relative (authors write both `kg/x.md` and `entities/x.md`
    // meaning a sibling) — pick whichever actually exists in the doc's workspace.
    const root = normalizeDocPath(ref.path, meta.path);
    const tree = await workspaceTree(meta.slug);
    if (tree.includes(root)) return { path: root, slug: meta.slug };
    if (meta.path) {
      const sibling = normalizeDocPath(`./${ref.path.replace(/^\.\//, "")}`, meta.path);
      if (tree.includes(sibling)) return { path: sibling, slug: meta.slug };
    }
    // Not in the tree — still open it (the doc tab shows "(not found)", which is louder
    // and more debuggable than a click that does nothing).
    return { path: root, slug: meta.slug };
  }
  if (ref.wikilink) {
    const re = wikilinkMatcher(ref.wikilink);
    // the doc's own workspace first, then home, then the rest of the mounted set
    const others = (await activeSlugs()).filter((s) => s !== meta.slug);
    const candidates = [...new Set<string | undefined>([meta.slug, undefined, ...others])];
    for (const ws of candidates) {
      const hit = (await workspaceTree(ws)).find((p) => re.test(p));
      if (hit) return { path: hit, slug: ws, type: re.exec(hit)?.[1] };
    }
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
};
export const DEFAULT_ENTITY_CHIP = { icon: "link", color: "var(--blue)", bg: "var(--bluebg)" };

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
    return (
      <span title={`No entity doc found for “${title}” in the mounted workspaces`}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "baseline",
          background: "var(--panel2)", border: "1px dashed var(--line)", borderRadius: 999,
          padding: "0.5px 9px 0.5px 7px", color: "var(--t3)", fontSize: "0.92em",
          fontWeight: 500, whiteSpace: "nowrap", lineHeight: 1.45 }}>
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

/** Workspace-internal link (schemeless href) — navigates the doc pane in place (or opens
 *  a tab outside one), resolving relative hrefs against the linking doc. Both renderers
 *  (MdxDoc's `a` mapping and the plain-Markdown fallback) emit this for internal links. */
export function InternalLink({ href, children }: { href: string; children?: ReactNode }) {
  const meta = useContext(DocMetaContext);
  const openEntity = useOpenEntity();
  return (
    <span role="link" onClick={() => openEntity({ path: normalizeDocPath(href.replace(/^\.\//, ""), meta.path) })}
      style={{ color: "var(--blue)", textDecoration: "underline", cursor: "pointer" }}>{children}</span>
  );
}

/** True when an href points inside the workspace (no scheme, not an anchor, not //host). */
export const isInternalHref = (href?: string): boolean =>
  Boolean(href) && !/^[a-z][a-z0-9+.-]*:/i.test(href!) && !href!.startsWith("#") && !href!.startsWith("//");
