/** docImages — what `![alt](src)` renders as, in BOTH doc renderers (Vexa-ai/vexa#1612).
 *
 *  Founder, 2026-09-06, walking a customer workspace README the agent had written: the page showed
 *  the alt text and a broken-image icon — *"we want to be able images"*.
 *
 *  THE RULE IS ABOUT WHERE THE BYTES LIVE, not about turning images on. A page's picture is a file
 *  in the workspace, fetched through the same owner- and membership-scoped door the page came from
 *  (`/api/workspace/asset`). It is never a hotlink: a document inside a bank's workspace must not
 *  send that bank's browser to a third party — that is a beacon they did not agree to, a request we
 *  cannot see, and an image that disappears the day someone else's CDN changes its mind.
 *
 *  So there are exactly two renders:
 *
 *   - a WORKSPACE path (`assets/oenb-logo.svg`, `./chart.png`) resolves against the doc's own
 *     directory and workspace and loads through the asset route. A path with nothing behind it says
 *     so in words rather than showing the browser's broken-image glyph, which is indistinguishable
 *     from a bug in us;
 *   - a REMOTE url renders a PLACEHOLDER that names it as external and offers to fetch it in. The
 *     offer is the product: one press stores the bytes under `assets/`, records the source, and
 *     rewrites the reference in the page, so the document ends up in the shape it should have been
 *     written in. Nothing is loaded from the remote host until somebody asks for it.
 */
"use client";
import { useContext, useEffect, useState, type CSSProperties } from "react";
import { Icon } from "./index";
import { DocMetaContext } from "./docRefs";
import { isInternalHref, normalizeDocPath } from "./docLinks";
import { WORKSPACE_COMMIT_EVENT } from "../canvas/actions";

/** The only schemes a placeholder can offer to fetch. A `data:` image carries its own bytes and has
 *  no source to record; anything else is not a thing we can GET. */
const FETCHABLE = /^https?:\/\//i;

/** The `src` an `<img>` in a workspace doc points at: the asset read route, scoped like every other
 *  workspace read. A URL and not a fetch, because the browser is the thing that loads pictures —
 *  handing it bytes we downloaded ourselves would give up caching, ranges and off-thread decode.
 *
 *  It lives HERE, not in `surfaces/workspaceApi`, for the reason `docLinks` reaches that module by
 *  `await import(...)`: the ui-kit renders markdown, and a static edge from it into the data-access
 *  layer would drag the HTTP client into every test that renders a paragraph. This is a string
 *  template with no client behind it, so it can simply be beside the renderer that needs it. */
export function workspaceAssetUrl(path: string, opts?: { slug?: string }): string {
  const q = opts?.slug ? `&slug=${encodeURIComponent(opts.slug)}` : "";
  return `/api/workspace/asset?path=${encodeURIComponent(path)}${q}`;
}

/** The host a reader is being asked to trust, for the placeholder's own sentence. */
export function externalHost(src: string): string {
  try {
    return new URL(src, "https://placeholder.invalid").hostname || src;
  } catch {
    return src;
  }
}

/** Point every `![…](<from>)` in a document at `<to>`. The reference is rewritten wherever it
 *  appears — markdown link, bare `<img src>`, or plain prose — because the reader pressed "fetch
 *  this image", not "fetch the third occurrence of this image". Exported for its test. */
export function rewriteImageReference(source: string, from: string, to: string): string {
  if (!from || from === to) return source;
  return source.split(from).join(to);
}

const frame: CSSProperties = {
  display: "flex", alignItems: "center", gap: 10, border: "1px dashed var(--line2)",
  borderRadius: 10, background: "var(--panel)", padding: "10px 13px", margin: "8px 0 12px",
  lineHeight: 1.45, color: "var(--t2)", fontSize: 12.5, maxWidth: "100%",
};

/** An image the workspace holds. `loading="lazy"` and a plain `<img>` on purpose: the browser is
 *  the thing that loads pictures, and the asset route answers an ETag so a page re-render costs a
 *  304 rather than a download. */
function WorkspaceImage({ path, slug, alt }: { path: string; slug?: string; alt?: string }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [path, slug]);
  if (broken) {
    // FAIL IN WORDS. The browser's broken-image glyph says "something is wrong with this page";
    // this says which file is missing, which is the only version of that sentence anyone can act on.
    return (
      <span data-image-missing={path} style={{ ...frame, display: "inline-flex" }}>
        <Icon name="alert" size={14} />
        <span>No <code style={{ fontFamily: "var(--mono)" }}>{path}</code> in this workspace{alt ? ` — “${alt}”` : ""}</span>
      </span>
    );
  }
  return (
    <img data-workspace-image={path} src={workspaceAssetUrl(path, { slug })} alt={alt ?? ""}
      loading="lazy" onError={() => setBroken(true)}
      style={{ maxWidth: "100%", height: "auto", borderRadius: 8, margin: "6px 0", display: "block" }} />
  );
}

/** A remote image, NOT loaded — named, and offered. */
function ExternalImage({ src, alt }: { src: string; alt?: string }) {
  const meta = useContext(DocMetaContext);
  const [state, setState] = useState<"offer" | "fetching" | "failed">("offer");
  const [stored, setStored] = useState<string | null>(null);
  const [error, setError] = useState<string>("");
  const fetchable = FETCHABLE.test(src);

  if (stored) return <WorkspaceImage path={stored} slug={meta.slug} alt={alt} />;

  const bring = async () => {
    setState("fetching");
    try {
      const api = await import("../surfaces/workspaceApi");
      const asset = await api.fetchWorkspaceAsset(src, { slug: meta.slug });
      // THE REFERENCE FOLLOWS THE BYTES. Storing the image and leaving the page pointing at the
      // remote host would fix this render and nothing else — the next reader, and every export of
      // the document, would go back out to the third party.
      if (meta.path) {
        const body = await api.readWorkspaceFile(meta.path, { slug: meta.slug });
        if (body !== null) {
          const next = rewriteImageReference(body, src, asset.path);
          if (next !== body) {
            await api.writeWorkspaceFile(meta.path, next, { slug: meta.slug });
            // THE PAGE ON SCREEN IS NOW BEHIND THE PAGE ON DISK. The shell re-reads the open
            // document on this event, which is the only durable fix: `stored` below survives until
            // the next MDX recompile (the workspace snapshot lands and the whole compiled tree is
            // replaced), and a reader whose picture reverted to a placeholder on its own would
            // reasonably conclude the fetch had failed.
            window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT));
          }
        }
      }
      setStored(asset.path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("failed");
    }
  };

  return (
    <span data-external-image={src} style={frame}>
      <Icon name="web" size={14} />
      <span style={{ minWidth: 0 }}>
        External image — it lives on <strong style={{ color: "var(--t1)" }}>{externalHost(src)}</strong>, not in this workspace{alt ? `: “${alt}”` : ""}.
        {state === "failed" && <span style={{ color: "var(--danger)" }}> Could not fetch it: {error}</span>}
      </span>
      {fetchable
        ? <button type="button" data-image-fetch onClick={() => void bring()} disabled={state === "fetching"}
            style={{ flex: "none", background: "transparent", border: "1px solid var(--line2)", borderRadius: 6,
              color: state === "fetching" ? "var(--t3)" : "var(--t1)", fontSize: 12, padding: "3px 9px",
              cursor: state === "fetching" ? "default" : "pointer" }}>
            {state === "fetching" ? "Fetching…" : "Fetch into the workspace"}
          </button>
        : <span style={{ flex: "none", color: "var(--t3)" }}>nothing to fetch</span>}
    </span>
  );
}

/** THE `img` BOTH RENDERERS USE. MdxDoc registers it in the MDX vocabulary; the plain-Markdown
 *  fallback emits it for `![alt](src)` — one component, so a doc that fails to compile as MDX shows
 *  the same picture and offers the same fetch. */
export function DocImage({ src, alt }: { src?: string; alt?: string }) {
  const meta = useContext(DocMetaContext);
  const href = (src ?? "").trim();
  if (!href) return <>{alt ?? ""}</>;
  if (!isInternalHref(href)) return <ExternalImage src={href} alt={alt} />;
  // A leading `/` is read as workspace-ROOT-relative, and a worker-visible mount path
  // (`/workspaces/<slug>/assets/x.png`) therefore misses — landing on the named-file message above,
  // which says which path was not found. That is the honest answer: translating a mount path needs
  // the async mount table (docLinks.fromWorkerPath) and an image cannot await, and guessing the
  // workspace off the directory layout is how a link opens somebody else's copy.
  // `./x` and `../x` go to `normalizeDocPath` INTACT — that is how it knows to resolve them against
  // the linking doc's directory. (`InternalLink` strips the `./` first and so resolves those from
  // the workspace root; it can afford to, because `resolveDocRef` retries a miss as a sibling. An
  // image has no second try: the file is where the reference says it is, or the page is missing it.)
  const path = href.startsWith("/") ? href.replace(/^\/+/, "") : normalizeDocPath(href, meta.path);
  return <WorkspaceImage path={path} slug={meta.slug} alt={alt} />;
}
