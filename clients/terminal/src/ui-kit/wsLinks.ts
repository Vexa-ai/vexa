/** wsLinks — the client half of PRD decision 26: cross-workspace links, resolved at read time.
 *
 *  `docLinks` resolves a link by SEARCHING the mounted workspaces' trees. That is right for the
 *  in-workspace `[[Title]]` form and structurally wrong for a link INTO another workspace, for one
 *  reason the client cannot fix on its own: it can tell "I found it" from "I didn't", and it can
 *  never tell **"it exists and is not yours"** from **"it is gone"**. Collapsing those two is how a
 *  product tells somebody their colleague's page was deleted when in fact they were never invited.
 *
 *  So `ws:` refs go to the server (`POST /api/links/resolve`) and come back with the answer:
 *
 *      readable   → a chip that opens the page in the panel
 *      not-yours  → a greyed chip: the title, and "in a workspace you don't have". No error.
 *      gone       → the last known title, plain text
 *
 *  Founder, 2026-09-02: *"If a workspace is not available, it's okay — by design."* Nothing here
 *  ever throws at a renderer: a failed request resolves to `gone`, which is the honest reading of
 *  "we could not find out", and it renders as text rather than as a broken chip.
 *
 *  BATCHED, because a document is a page of links and a request per link is a burst of them against
 *  the same three directories. Every ref asked for inside one tick goes up as one call.
 */

/** The cross-workspace prefix. Lowercase only — the same rule the server applies, so `[[WS:…]]` and
 *  `[[ws:…]]` cannot become two spellings of one link. */
export const WS_PREFIX = "ws:";

/** A workspace id: 10 chars of lowercase RFC-4648 base32 (`shared/workspace_id.py`). No `0`/`1`, so
 *  nothing in it can be misread as a letter in a link somebody types off a screen. */
export const WORKSPACE_ID = /^[a-z2-7]{10}$/;
export const isWorkspaceId = (v: string): boolean => WORKSPACE_ID.test(v ?? "");

export interface WsRef { workspace: string; target: string }

/** Parse the text inside `[[ ]]`. Returns null for the in-workspace form — which is every link that
 *  is not a well-formed `ws:` ref, including a malformed one: the server is the only thing that can
 *  say what a malformed ref means, and it answers `gone`. */
export function parseWsRef(inner: string): WsRef | null {
  const raw = (inner ?? "").trim();
  if (!raw.toLowerCase().startsWith(WS_PREFIX)) return null;
  const rest = raw.slice(WS_PREFIX.length).replace(/^\/+/, "");
  const cut = rest.indexOf("/");
  if (cut <= 0) return null;
  const workspace = rest.slice(0, cut).trim();
  const target = rest.slice(cut + 1).trim();
  return isWorkspaceId(workspace) && target ? { workspace, target } : null;
}

/** True when this wikilink title is a cross-workspace ref (the branch `Wikilink` takes). */
export const isWsRef = (inner: string): boolean => (inner ?? "").trim().toLowerCase().startsWith(WS_PREFIX);

export function formatWsRef(workspace: string, target: string): string {
  return `[[${WS_PREFIX}${workspace}/${target}]]`;
}

// ── the canonical URL ─────────────────────────────────────────────────────────────────────────
/** `/w/<workspace-id>/<path>` — the ONE URL for a file: valid in mail, in chat and inside a doc,
 *  and still valid after the workspace is renamed. */
export const CANONICAL_URL = /^\/w\/([a-z2-7]{10})(?:\/(.*))?$/;

export function canonicalUrl(workspace: string, path = ""): string {
  const p = (path ?? "").replace(/^\/+/, "");
  return p ? `/w/${workspace}/${p}` : `/w/${workspace}`;
}

/** `{workspace, path}` for a canonical URL, else null. Query and fragment are dropped: a mail
 *  client that appends tracking to a link must not make it unreadable. */
export function parseCanonicalUrl(url: string): WsRef | null {
  const clean = (url ?? "").split("#")[0].split("?")[0];
  const m = CANONICAL_URL.exec(clean);
  if (!m) return null;
  return { workspace: m[1], target: m[2] ?? "" };
}

// ── resolution ────────────────────────────────────────────────────────────────────────────────
export type LinkAccess = "readable" | "not-yours" | "gone";

export interface ResolvedLink {
  ref: string;
  title: string;
  url: string | null;
  access: LinkAccess;
  /** The NAME of the workspace it lives in — what a greyed chip says you don't have. */
  workspace?: string | null;
  /** Present only when readable AND found: what the panel opens. */
  path?: string;
  slug?: string;
  missing?: boolean;
  /** Several readable pages match a bare slug (#1783): the reader picks one. */
  ambiguous?: boolean;
  candidates?: { title: string; path?: string; slug?: string; workspace?: string | null; url?: string | null }[];
}

/** `id` and `name` are null on a `not-yours` answer: a reader with no claim on a workspace is told
 *  that it is not theirs and nothing else. `writable` is this reader's write permission there. */
export interface WorkspaceIdentity { id: string | null; name: string | null; kind: string | null; slug?: string | null; access: LinkAccess; writable?: boolean }

/** A ref we could not ask about. `gone` and not an error, deliberately: a failed request is
 *  "we could not find out", and the honest render of that is the last thing we know — the title
 *  the writer typed — as plain text. */
const unknown = (ref: string): ResolvedLink => ({
  ref, title: humanize(parseWsRef(ref)?.target ?? ref), url: null, access: "gone", workspace: null,
});

/** `olga-avramenko` → `Olga Avramenko`. What a ref can be read as before anything resolves it —
 *  and the only title available for a page in a workspace this reader may not open. */
export function humanize(target: string): string {
  const stem = (target ?? "").replace(/\/+$/, "").split("/").pop() ?? "";
  const base = stem.replace(/\.[a-z0-9]{1,5}$/i, "");
  const words = base.split(/[-_]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1));
  return words.join(" ") || base || target;
}

/** The names we have already read, for a caller that must decide synchronously (a tab label). */
const names = new Map<string, string>();

const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; v: ResolvedLink }>();
const nameCache = new Map<string, { at: number; p: Promise<WorkspaceIdentity | null> }>();

/** Drop the caches — an agent creates entities while a doc is open, and a group can be renamed
 *  under a reader. Mirrors `invalidateDocLinkCaches`. */
export function invalidateWsLinkCaches(): void {
  cache.clear();
  nameCache.clear();
  names.clear();
}

type Waiter = { ref: string; resolve: (v: ResolvedLink) => void };
const pending = new Map<string, Waiter[]>();   // keyed by the reader's own workspace slug ("" = default)
let scheduled = false;

/** The batch flush: every ref asked for inside one tick becomes ONE request per reader-workspace. */
function schedule(): void {
  if (scheduled) return;
  scheduled = true;
  const run = () => {
    scheduled = false;
    const batches = [...pending.entries()];
    pending.clear();
    for (const [key, waiters] of batches) void flush(key, waiters);
  };
  // A microtask would coalesce only synchronous callers; a doc's chips mount across a render pass,
  // so a macrotask of 0 is what actually collects them.
  setTimeout(run, 0);
}

async function flush(key: string, waiters: Waiter[]): Promise<void> {
  const refs = [...new Set(waiters.map((w) => w.ref))];
  let results: ResolvedLink[] = [];
  try {
    const api = await import("../data/workspaceApi");
    results = await api.resolveLinks(refs, key || undefined);
  } catch {
    results = [];                                   // never throws at a renderer — see `unknown`
  }
  const by = new Map(results.map((r) => [r.ref, r]));
  for (const w of waiters) {
    const v = by.get(w.ref) ?? unknown(w.ref);
    cache.set(`${key}\u0000${w.ref}`, { at: Date.now(), v });
    w.resolve(v);
  }
}

/** Resolve one ref for one reader. Batched, cached, and it never rejects. */
export function resolveLink(ref: string, slug?: string): Promise<ResolvedLink> {
  const key = slug ?? "";
  const hit = cache.get(`${key}\u0000${ref}`);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return Promise.resolve(hit.v);
  return new Promise<ResolvedLink>((resolve) => {
    const list = pending.get(key);
    if (list) list.push({ ref, resolve });
    else pending.set(key, [{ ref, resolve }]);
    schedule();
  });
}

/** A workspace's identity by the slug the client already holds — the NAME behind a mount.
 *
 *  This is the whole of F49: the chat header printed `126`, which is a directory name showing
 *  through. Names live in the registry precisely because slugs and directories change and names
 *  are what a person reads. */
export function workspaceBySlug(slug: string): Promise<WorkspaceIdentity | null> {
  const hit = nameCache.get(slug);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.p;
  const p = import("../data/workspaceApi")
    .then((api) => api.readWorkspaceBySlug(slug))
    .catch(() => null);
  nameCache.set(slug, { at: Date.now(), p });
  return p;
}

/** The label for a mounted workspace: its registry NAME once known, else the slug itself.
 *  Synchronous and honest — a slug is a worse label than a name and a better one than nothing. */
export function workspaceLabel(slug: string): string {
  return names.get(slug) ?? slug;
}

/** Fill the label snapshot for a set of slugs. Idempotent; failures leave the slug in place. */
export async function primeWorkspaceNames(slugs: string[]): Promise<void> {
  await Promise.all([...new Set(slugs.filter(Boolean))].map(async (slug) => {
    const rec = await workspaceBySlug(slug);
    if (rec?.name) names.set(slug, rec.name);
  }));
}
