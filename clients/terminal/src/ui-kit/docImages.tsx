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
 *
 *  AND WHEN THE OFFER FAILS, IT IS STILL THE PRODUCT (Vexa-ai/vexa#1624). Founder, the same day, on
 *  the OeNB README: the agent had written a Wikimedia address that answers 404, and pressing the
 *  offer printed the route and both status codes in red. A reader cannot act on a stack trace. So
 *  the failure is one sentence about the picture — *This image does not exist at that address (the
 *  site answered 404)* — and the two moves that exist from there: **Find it**, which queues a
 *  same-target act on this chat to go and get the real one, and **Remove the link**, which commits
 *  the page without it.
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

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** TAKE THE PICTURE OUT AND LEAVE THE PAGE (Vexa-ai/vexa#1624) — what "Remove the link" writes.
 *
 *  The whole reference goes: `![alt](src)` and `<img src="src">`, everywhere they appear, not just
 *  the address inside them, because a `![OeNB logo]()` is a broken picture wearing a different
 *  glyph. Everything around it stays — the sentence was not the mistake, the address was — and the
 *  only tidying is the hole itself: a trailing space, and the blank line a picture on a line of its
 *  own leaves behind. Sibling of `rewriteImageReference`, and exported for the same reason. */
export function removeImageReference(source: string, src: string): string {
  if (!src) return source;
  const u = escapeRe(src);
  return source
    .replace(new RegExp(`!\\[[^\\]\\n]*\\]\\(\\s*${u}[^)]*\\)`, "g"), "")
    .replace(new RegExp(`<img\\b[^>]*?src\\s*=\\s*(["'])${u}\\1[^>]*?/?>`, "gi"), "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n");
}

/** WHAT "FIND IT" ASKS FOR. One sentence, in the person's shape not ours: the picture they can see
 *  is missing is named by its own alt text, the destination is the one place a workspace image ever
 *  lives, and the page's reference is part of the job — an image fetched into `assets/` with the
 *  document still pointing at the dead address has fixed nothing the reader can see. */
export function findItInstruction(alt: string | undefined, src: string): string {
  const what = (alt ?? "").trim();
  return `find the real ${what ? `${what} ` : ""}image, fetch it into assets/, and fix the link` +
    ` — the page points at ${src}, which does not answer.`;
}

/** THE FAILED FETCH, IN WORDS (Vexa-ai/vexa#1624).
 *
 *  The reader who pressed the offer on the OeNB logo was shown, in red: *Could not fetch it:
 *  /api/workspace/asset → 400: https://upload.wikimedia.org/… answered 404.* That is a route, two
 *  status codes and a URL — the operator channel, printed at a person, who can only read it as "the
 *  button is broken". What actually happened is one sentence long and the route now carries the
 *  upstream code so it can be said: the picture is not there.
 *
 *  Duck-typed on purpose. The error is an `ApiError` from `surfaces/apiClient`, and a static import
 *  of that module from the ui-kit would drag the HTTP client into every test that renders a
 *  paragraph — the same reason `workspaceAssetUrl` lives here as a string template and `docLinks`
 *  reaches the data layer through `await import(...)`. Reading two fields off a shape needs no type
 *  from it. */
export function fetchFailureLine(err: unknown, host: string): string {
  const e = err as { status?: number; detail?: string; message?: string;
    body?: { detail?: { upstream_status?: number | null } } } | null;
  const upstream = e?.body?.detail?.upstream_status ?? null;
  if (upstream) {
    if (upstream === 404 || upstream === 410) return `This image does not exist at that address (the site answered ${upstream}).`;
    if (upstream === 401 || upstream === 403) return `${host} will not hand this image over (it answered ${upstream}).`;
    if (upstream >= 500) return `${host} is failing right now (it answered ${upstream}).`;
    return `That address did not answer with an image (the site answered ${upstream}).`;
  }
  if (e?.status === 502) return `Nothing answered at ${host}.`;
  // A 400 is OUR refusal of the address itself, and it is already a sentence written for a person
  // (`asset_source.fetch_refusal` — "refusing 'redis' — that is an internal service name…").
  if (e?.status === 400 && e.detail) return e.detail;
  return `Could not fetch it: ${e?.message ?? String(err)}`;
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

/** The small controls in the placeholder's right-hand end — the offer, and the two acts a failure
 *  replaces it with. One shape, so a failure is answered by controls of the same weight as the
 *  offer that failed rather than by a line of red text with nothing to press. */
const chipButton = (tone: "plain" | "danger" = "plain"): CSSProperties => ({
  flex: "none", background: "transparent", border: "1px solid var(--line2)", borderRadius: 6,
  color: tone === "danger" ? "var(--t2)" : "var(--t1)", fontSize: 12, padding: "3px 9px",
  cursor: "pointer",
});

/** A remote image, NOT loaded — named, and offered. */
function ExternalImage({ src, alt }: { src: string; alt?: string }) {
  const meta = useContext(DocMetaContext);
  const [state, setState] = useState<"offer" | "fetching" | "failed">("offer");
  const [stored, setStored] = useState<string | null>(null);
  const [failure, setFailure] = useState<string>("");
  /** which act the reader pressed on the failure, so the box says what it did rather than sitting
   *  there looking unpressed — the #1604 rule, in the smallest form this control has room for */
  const [took, setTook] = useState<"" | "find" | "remove">("");
  const fetchable = FETCHABLE.test(src);
  // AN ACT NEEDS A TARGET AND WE NEVER GUESS ONE (F63). Without the doc's own path there is no page
  // to send the chat at and no file to edit, so the two acts are not offered at all.
  const actable = !!meta.path;

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
      setFailure(fetchFailureLine(e, externalHost(src)));
      setState("failed");
    }
  };

  /** FIND IT — the same chat, the same page, one act (Vexa-ai/vexa#1624 · #1610's inbox). It goes
   *  through `postIntent`, not through a hand-rolled POST, so it is a same-target act like every
   *  other: it queues behind whatever that chat is doing instead of being refused, it raises the
   *  act state the page's own controls wear, and the page it names is the one the reader is on.
   *
   *  Reached by `await import` for the reason the module header gives about `workspaceApi`: the
   *  ui-kit renders markdown, and a static edge from it into a shell module would pull the chat
   *  into every test that renders a paragraph. */
  const findIt = async () => {
    if (!meta.path) return;
    setTook("find");
    const { postIntent } = await import("../minutes/extend");
    postIntent({ kind: "extend", workspace: meta.slug, path: meta.path,
      instruction: findItInstruction(alt, src) });
  };

  /** REMOVE THE LINK — a commit, not a re-render. The reader's judgement is that the picture is not
   *  coming, and the page should stop claiming otherwise for the next reader and for every export
   *  of it; hiding the placeholder in this one tab would leave the document exactly as wrong as it
   *  was. Same three steps as taking the offer, in the other direction. */
  const dropIt = async () => {
    if (!meta.path) return;
    setTook("remove");
    try {
      const api = await import("../surfaces/workspaceApi");
      const body = await api.readWorkspaceFile(meta.path, { slug: meta.slug });
      if (body === null) return;
      const next = removeImageReference(body, src);
      if (next === body) return;
      await api.writeWorkspaceFile(meta.path, next, { slug: meta.slug });
      window.dispatchEvent(new CustomEvent(WORKSPACE_COMMIT_EVENT));
    } catch (e) {
      setTook("");
      setFailure(fetchFailureLine(e, externalHost(src)));
    }
  };

  const failed = state === "failed";
  return (
    <span data-external-image={src} style={{ ...frame, flexWrap: "wrap" }}>
      <Icon name={failed ? "alert" : "web"} size={14} />
      <span style={{ minWidth: 0, flex: "1 1 55%" }}>
        External image — it lives on <strong style={{ color: "var(--t1)" }}>{externalHost(src)}</strong>, not in this workspace{alt ? `: “${alt}”` : ""}.
        {failed && <span data-image-failed style={{ display: "block", marginTop: 3, color: "var(--danger)" }}>{failure}</span>}
      </span>
      {failed
        // THE FAILURE IS A PLACE TO ACT FROM, not a dead end. Two moves and they are the only two
        // there are: get the right picture, or stop the page promising one.
        ? <span style={{ flex: "none", display: "flex", gap: 6 }}>
            {took
              ? <span data-image-took={took} style={{ color: "var(--t3)" }}>
                  {took === "find" ? "Asked this chat to find it" : "Removing it from the page…"}
                </span>
              : actable && <>
                  <button type="button" data-image-find onClick={() => void findIt()} style={chipButton()}
                    title="Ask this chat to find the real image, fetch it into the workspace and fix the link">
                    Find it
                  </button>
                  <button type="button" data-image-drop onClick={() => void dropIt()} style={chipButton("danger")}
                    title="Take the image out of the page — the text stays">
                    Remove the link
                  </button>
                </>}
          </span>
        : fetchable
          ? <button type="button" data-image-fetch onClick={() => void bring()} disabled={state === "fetching"}
              style={{ ...chipButton(), color: state === "fetching" ? "var(--t3)" : "var(--t1)",
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
