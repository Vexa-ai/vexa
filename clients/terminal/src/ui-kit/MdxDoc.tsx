/** MdxDoc — runtime MDX renderer for workspace (kg/) markdown.
 *
 *  Mintlify-style model: markdown + a CLOSED registry of declarative components
 *  (<Note>, <Warning>, <Card>, <CardGroup>, <Steps>/<Step>, <Tabs>/<Tab>) rendered
 *  with the terminal's design tokens. Compilation happens in the browser from the
 *  file's string content via @mdx-js/mdx (MIT) — no build step, so agent-written
 *  files render immediately after every edit.
 *
 *  Failure containment: agent-authored MDX can be malformed (stray `<`, unbalanced
 *  braces). If compile/run throws, we fall back to the legacy <Markdown> renderer
 *  so the doc always displays — worst case it loses interactivity, never the page.
 */
"use client";
import { isValidElement, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import * as runtime from "react/jsx-runtime";
import { evaluate } from "@mdx-js/mdx";
import remarkGfm from "remark-gfm";
import { Markdown, stripHtmlComments } from "./Markdown";
import { DocImage } from "./docImages";
import { MermaidDiagram, isMermaidFence } from "./docDiagrams";
import { Icon } from "./index";
import {
  Card, CardGroup, DocMetaContext, DocNavContext, DocPath, ENTITY_CHIP, DEFAULT_ENTITY_CHIP, InternalLink,
  Wikilink, WorkspaceRef, isDistinctiveWorkspaceToken, isInternalHref, knownWorkspaces, lookupWorkspace,
  primeKnownWorkspaces, type DocNavigate,
} from "./docLinks";
import { OPEN_MEETING_EVENT } from "../canvas/actions";
import { registry } from "../contributions";
import { splitTranscriptSlots, TRANSCRIPT_WIDGET_KIND } from "./transcriptSlot";
import { POLICY_KIND, PolicyRules, ViewSource, declaredKind, splitFrontmatter } from "./policyDoc";

// Link/wikilink resolution + the entity chips live in ./docLinks (ONE resolver shared with
// the plain-Markdown fallback and the workbench event handler). Re-exported for existing
// importers (surfaces/workspace.tsx, mdx-demo).
export { DocMetaContext, DocNavContext, ENTITY_CHIP, DEFAULT_ENTITY_CHIP, type DocNavigate };

// ── component registry (closed vocabulary — mirrors Mintlify tag names) ─────────

function Callout({ tone, icon, children }: { tone: "blue" | "accent"; icon: string; children?: ReactNode }) {
  const color = tone === "blue" ? "var(--blue)" : "var(--accent)";
  return (
    <div style={{ display: "flex", gap: 10, border: "1px solid var(--line)", borderLeft: `3px solid ${color}`, borderRadius: 8, background: "var(--panel)", padding: "10px 13px", margin: "8px 0 12px", lineHeight: 1.55 }}>
      <span style={{ color, flex: "none", marginTop: 2 }}><Icon name={icon} size={14} /></span>
      <div style={{ color: "var(--t2)", minWidth: 0 }}>{children}</div>
    </div>
  );
}
const Note = ({ children }: { children?: ReactNode }) => <Callout tone="blue" icon="info">{children}</Callout>;
const Warning = ({ children }: { children?: ReactNode }) => <Callout tone="accent" icon="alert">{children}</Callout>;

// Card + CardGroup live in ./docLinks — shared with the plain-Markdown fallback so the
// same link-card UI renders whether or not the doc compiles as MDX.

function Steps({ children }: { children?: ReactNode }) {
  const items = Array.isArray(children) ? children : [children];
  return (
    <div style={{ margin: "8px 0 12px", display: "flex", flexDirection: "column" }}>
      {items.filter(Boolean).map((child, i) => (
        <div key={i} style={{ display: "flex", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: "none" }}>
            <div style={{ width: 22, height: 22, borderRadius: 11, background: "var(--panel2)", border: "1px solid var(--line2)", color: "var(--t1)", fontSize: 11.5, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center" }}>{i + 1}</div>
            {i < items.length - 1 && <div style={{ width: 1, flex: 1, background: "var(--line)" }} />}
          </div>
          <div style={{ paddingBottom: 14, minWidth: 0, flex: 1 }}>{child}</div>
        </div>
      ))}
    </div>
  );
}

function Step({ title, children }: { title?: string; children?: ReactNode }) {
  return (
    <div>
      {title && <div style={{ fontWeight: 600, color: "var(--t1)", fontSize: 13.5, marginBottom: 4, lineHeight: "22px" }}>{title}</div>}
      <div style={{ color: "var(--t2)", lineHeight: 1.55 }}>{children}</div>
    </div>
  );
}

function Tabs({ children }: { children?: ReactNode }) {
  const items = (Array.isArray(children) ? children : [children]).filter(Boolean) as Array<{ props?: { title?: string; children?: ReactNode } }>;
  const [active, setActive] = useState(0);
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, margin: "8px 0 12px", overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 2, background: "var(--panel)", borderBottom: "1px solid var(--line)", padding: "4px 6px" }}>
        {items.map((t, i) => (
          <button key={i} onClick={() => setActive(i)}
            style={{ border: "none", background: i === active ? "var(--panel2)" : "transparent", color: i === active ? "var(--t1)" : "var(--t3)", fontSize: 12.5, fontWeight: i === active ? 600 : 400, padding: "5px 11px", borderRadius: 7, cursor: "pointer" }}>
            {t.props?.title ?? `Tab ${i + 1}`}
          </button>
        ))}
      </div>
      <div style={{ padding: "11px 14px" }}>{items[active]?.props?.children}</div>
    </div>
  );
}
const Tab = ({ children }: { title?: string; children?: ReactNode }) => <>{children}</>;

// ── standard element mapping — matches the legacy Markdown.tsx look ──────────────
/** The `<code class="language-x">…</code>` inside a `<pre>`, if that is what this is. mdast→hast
 *  spells the class as an ARRAY and hast-util-to-jsx-runtime hands it over as a string, so both are
 *  read; a `<pre>` with anything other than one code child (or with element children rather than a
 *  string of source) is not a fence and gets no opinion from us. */
function fencedCode(children: ReactNode): { lang: string; source: string } | null {
  const node = Array.isArray(children) ? children.find(isValidElement) : children;
  if (!isValidElement(node)) return null;
  const props = node.props as { className?: string | string[]; children?: ReactNode };
  const cls = Array.isArray(props.className) ? props.className.join(" ") : props.className ?? "";
  const lang = /(?:^|\s)language-([\w-]+)/.exec(cls)?.[1];
  if (!lang || typeof props.children !== "string") return null;
  return { lang, source: props.children.replace(/\n$/, "") };
}

const HEADING_SIZE: Record<number, number> = { 1: 18, 2: 16, 3: 14.5, 4: 13.5 };
const h = (lvl: number) => ({ children }: { children?: ReactNode }) => (
  <div style={{ fontSize: HEADING_SIZE[lvl], fontWeight: 600, color: "var(--t1)", lineHeight: 1.3, margin: lvl <= 2 ? "12px 0 6px" : "10px 0 4px" }}>{children}</div>
);

const htmlComponents = {
  h1: h(1), h2: h(2), h3: h(3), h4: h(4),
  // A PAGE'S PICTURE IS A WORKSPACE FILE (#1612). Left as a bare <img>, `![logo](assets/x.svg)`
  // resolves against the TERMINAL's URL and breaks, and `![logo](https://…)` hotlinks a third party
  // out of a customer's document. DocImage does neither: workspace paths load through the scoped
  // asset route, remote ones render as an offer to fetch them in.
  img: DocImage,
  p: ({ children }: { children?: ReactNode }) => <p style={{ margin: "0 0 8px", lineHeight: 1.6 }}>{children}</p>,
  a: ({ href, children }: { href?: string; children?: ReactNode }) => {
    // Meeting deep-link (`?meeting=<id>`, relative or absolute) → open the meeting canvas (transcript +
    // recording) in-app, no reload. The same URL also cold-opens the meeting via App.tsx (portable).
    const mref = href?.match(/[?&]meeting=([^&#]+)/);
    if (mref) {
      const ref = decodeURIComponent(mref[1]);
      return <span role="link" onClick={() => window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref } }))}
        style={{ color: "var(--blue)", textDecoration: "underline", cursor: "pointer" }}>{children}</span>;
    }
    // Workspace-internal link (no scheme, not an anchor) → navigate the doc pane in place
    // (or open a tab outside a doc pane), same path the Wikilink chip uses. Relative hrefs
    // resolve against the linking doc's directory. External links open a browser tab.
    if (href && isInternalHref(href)) return <InternalLink href={href}>{children}</InternalLink>;
    // external: only http(s) and #anchors keep a live href — javascript:/data:/;
    // //host from untrusted docs render as inert text
    const safeHref = href && (/^https?:/i.test(href) || href.startsWith("#")) ? href : undefined;
    if (!safeHref) return <span style={{ color: "var(--blue)" }}>{children}</span>;
    return <a href={safeHref} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "underline" }}>{children}</a>;
  },
  code: ({ children }: { children?: ReactNode }) => (
    <code style={{ fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px", color: "var(--t1)" }}>{children}</code>
  ),
  // A FENCE CAN BE A PICTURE (#1617). Markdown gives a fenced block to `pre` wrapping a `code`
  // whose class names the language, so this is the only seam where ```mermaid can be told from
  // ```bash — and the fence's language is the author's whole statement of intent. Everything else
  // still renders as the code block it is.
  pre: ({ children }: { children?: ReactNode }) => {
    const fence = fencedCode(children);
    if (fence && isMermaidFence(fence.lang)) return <MermaidDiagram source={fence.source} />;
    return (
      <pre style={{ fontFamily: "var(--mono)", fontSize: 12, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", margin: "6px 0 10px", overflowX: "auto", lineHeight: 1.5, color: "var(--t1)" }}>{children}</pre>
    );
  },
  ul: ({ children }: { children?: ReactNode }) => <ul style={{ margin: "4px 0 8px", paddingLeft: 20, display: "flex", flexDirection: "column", gap: 2 }}>{children}</ul>,
  ol: ({ children }: { children?: ReactNode }) => <ol style={{ margin: "4px 0 8px", paddingLeft: 20, display: "flex", flexDirection: "column", gap: 2 }}>{children}</ol>,
  li: ({ children }: { children?: ReactNode }) => <li style={{ lineHeight: 1.55 }}>{children}</li>,
  blockquote: ({ children }: { children?: ReactNode }) => (
    <blockquote style={{ borderLeft: "3px solid var(--line2)", paddingLeft: 12, margin: "6px 0 8px", color: "var(--t2)", lineHeight: 1.55 }}>{children}</blockquote>
  ),
  hr: () => <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "12px 0" }} />,
  table: ({ children }: { children?: ReactNode }) => (
    <table style={{ width: "100%", borderCollapse: "collapse", margin: "6px 0 10px", color: "var(--t1)", lineHeight: 1.45 }}>{children}</table>
  ),
  th: ({ children, style }: { children?: ReactNode; style?: CSSProperties }) => (
    <th style={{ background: "var(--panel)", border: "1px solid var(--line2)", padding: "6px 9px", color: "var(--t1)", fontWeight: 600, ...style }}>{children}</th>
  ),
  td: ({ children, style }: { children?: ReactNode; style?: CSSProperties }) => (
    <td style={{ border: "1px solid var(--line)", padding: "6px 9px", color: "var(--t2)", verticalAlign: "top", ...style }}>{children}</td>
  ),
};

export const MDX_COMPONENTS = { ...htmlComponents, ViewSource, Note, Warning, Card, CardGroup, Steps, Step, Tabs, Tab, Wikilink, DocPath, WorkspaceRef };

// ── security: forbid executable MDX ──────────────────────────────────────────
// kg/ markdown is agent-written from meeting transcripts and external content, so it
// is untrusted input. The component registry closes which TAGS resolve, but MDX
// expressions (`{...}`), ESM (`import`/`export`), and expression-valued attributes
// are arbitrary JS run in the viewer's session — reject them at the syntax tree and
// let the throw route into the plain-Markdown fallback below.
const FORBIDDEN_MDX_NODES = new Set(["mdxjsEsm", "mdxFlowExpression", "mdxTextExpression"]);
function assertNoExecutableMdx(node: { type?: string; attributes?: unknown[]; children?: unknown[] }): void {
  if (node.type && FORBIDDEN_MDX_NODES.has(node.type)) throw new Error(`executable MDX (${node.type}) is not allowed in workspace docs`);
  for (const attr of (node.attributes ?? []) as { type?: string; value?: { type?: string; value?: string } }[]) {
    if (attr?.type === "mdxJsxExpressionAttribute")
      throw new Error("expression-valued JSX attributes are not allowed in workspace docs");
    if (attr?.value?.type === "mdxJsxAttributeValueExpression") {
      // A LITERAL is data, not code: `cols={2}`, `open={true}` are the idiom agents write and
      // carry no execution risk. Anything with an identifier, call or member access is refused.
      const raw = String(attr.value.value ?? "").trim();
      if (!/^(-?\d+(\.\d+)?|true|false|null|'[^'\\]*'|"[^"\\]*")$/.test(raw))
        throw new Error("expression-valued JSX attributes are not allowed in workspace docs");
    }
  }
  for (const child of (node.children ?? []) as { type?: string }[]) assertNoExecutableMdx(child);
}
function remarkForbidExecutable() {
  return (tree: { type?: string; children?: unknown[] }) => assertNoExecutableMdx(tree);
}

// ── prose preprocessing (fences untouched) ───────────────────────────────────────
// 1. escape `<` that doesn't start a known tag — agent-written docs routinely carry raw
//    angle-bracket text (`<meeting_id>`, `a<b`, `<url>`) that would otherwise abort the
//    whole MDX compile and downgrade the doc to the plain renderer;
// 2. rewrite [[Title]] → <Wikilink title="Title" /> (after escaping, so the injected tag
//    survives);
// 3. rewrite a doc PATH → <DocPath path="…" /> so the file the agent names is clickable.
const KNOWN_TAGS = "Note|Warning|CardGroup|Card|Steps|Step|Tabs|Tab|Wikilink|DocPath|WorkspaceRef|ViewSource" +
  // no single-letter html tags (b, i): `a<b then` in prose is far likelier than a raw
  // <b> tag, and an unclosed <b would abort the compile this pass exists to save
  "|a\\b|br|blockquote|code|details|div|em|h[1-6]|hr|img|kbd|li|ol|p\\b|pre|span|strong|sub|summary|sup|table|tbody|td|th|thead|tr|ul";
const UNKNOWN_TAG_OPEN = new RegExp(`<(?!/?(?:${KNOWN_TAGS})(?:[\\s/>]|$))`, "g");
export function escapeUnknownTags(seg: string): string {
  return seg.replace(UNKNOWN_TAG_OPEN, "\\<");
}
// ── what a reply names, and what it must therefore link to ──────────────────────
// The founder asked the agent to "reference workspace with its readme". The reply named the
// workspace in bold and its README as inline code, and neither was clickable: "no reference, and
// when reference it's not interactive." Three spellings carry a reference, so three are recognized:
//
//   1. an ABSOLUTE mount path   `/workspaces/<slug>/README.md`   — inline code AND prose
//   2. a RELATIVE doc path      `kg/entities/company/x.md`       — inline code only, and only
//                                                                  once it resolves in a tree
//   3. a WORKSPACE name         **vexa-team-3183d1**             — bold, inline code, or (when
//                                                                  distinctive) bare prose
//
// FENCED blocks are never touched. A fence is a transcript of literal text — a shell command, a
// file listing, a snippet someone is meant to copy — and turning a word inside one into a chip
// falsifies what it says. Inline code is the opposite: it is the agent's own idiom for NAMING a
// file, which is exactly what has to become clickable.

/** Inline code that names a workspace doc: an absolute mount path (always chipped — unambiguous),
 *  or a bare workspace-relative path ending `.md` (chipped only once DocPath finds it in a tree,
 *  so `package.json` and prose fragments stay plain monospace). */
export const DOC_PATH_IN_CODE =
  /^(?:\/(?:[\w.-]+\/)*?workspaces\/[\w.-]+\/[\w./ -]+|[\w.-]+(?:\/[\w.\- ]+)*\.(?:md|markdown|mdx))$/;
/** The same absolute path, scanned inside PROSE. Ends on a word character so trailing sentence
 *  punctuation stays in the sentence — and NO space is allowed inside it: prose has no delimiter,
 *  so a space-tolerant class swallows the rest of the sentence into the chip. (Inline code IS
 *  delimited, which is why DOC_PATH_IN_CODE can afford to allow one.) */
const WORKER_PATH_IN_PROSE = /\/(?:[\w.-]+\/)*?workspaces\/[\w.-]+\/[\w./-]*[\w]/g;
/** A bold run — the spelling the founder's reply used for the workspace it was about. */
const BOLD_RUN = /\*\*([^*\n]+)\*\*/g;
/** A bare slug-shaped token in prose (filtered against the KNOWN set before it becomes a chip). */
const BARE_TOKEN = /[A-Za-z0-9][\w-]*/g;

function docPathsInProse(seg: string): string {
  return seg.replace(WORKER_PATH_IN_PROSE, (m: string, offset: number) => {
    const prev = offset > 0 ? seg[offset - 1] : "";
    // `](…)` is already a markdown link and `="…"` is an attribute we just injected; a word char or
    // a slash before it means we're mid-token, not at the start of a path.
    if (seg.slice(Math.max(0, offset - 2), offset) === "](" || /[\w`"'=/]/.test(prev)) return m;
    return `<DocPath path=${JSON.stringify(m)} />`;
  });
}

/** Workspace names → chips, matched ONLY against the closed known set (lookupWorkspace). Bold and
 *  inline-code mentions are deliberate and match any known token; a BARE prose word must also be
 *  distinctive (carry a `-` or `_`) so "personal notes" never sprouts a chip. */
function workspaceRefsInProse(seg: string): string {
  if (!knownWorkspaces().length) return seg;      // snapshot cold — emit nothing rather than guess
  const chip = (t: string) => `<WorkspaceRef token=${JSON.stringify(t)} />`;
  const bold = seg.replace(BOLD_RUN, (m, inner: string) => (lookupWorkspace(inner) ? chip(inner.trim()) : m));
  return bold.replace(BARE_TOKEN, (m: string, offset: number) => {
    if (!isDistinctiveWorkspaceToken(m) || !lookupWorkspace(m)) return m;
    // Never rewrite inside a tag we just injected (`token="…"`, `path="…"`), inside a markdown
    // link's text or target, or mid-path — those are structure, not a mention.
    const before = bold.slice(0, offset);
    if (before.lastIndexOf("<") > before.lastIndexOf(">")) return m;
    const prev = offset > 0 ? bold[offset - 1] : "";
    if ("/\"'`[(=".includes(prev) || bold[offset + m.length] === "/") return m;
    return chip(m);
  });
}

/** Rewrite every reference spelling into its interactive component, fences excepted. */
export function transformDocRefs(src: string): string {
  // split out fenced code blocks and inline code; odd indices are one or the other
  return src.split(/(```[\s\S]*?```|`[^`]*`)/g).map((seg, i) => {
    if (i % 2 === 0) {
      const prose = escapeUnknownTags(seg)
        .replace(/\[\[([^\]]+)\]\]/g, (_m, t: string) => `<Wikilink title=${JSON.stringify(t)} />`);
      return workspaceRefsInProse(docPathsInProse(prose));
    }
    if (seg.startsWith("```")) return seg;                         // fenced: literal, always
    const code = seg.slice(1, -1);
    if (DOC_PATH_IN_CODE.test(code)) return `<DocPath path=${JSON.stringify(code)} />`;
    if (lookupWorkspace(code)) return `<WorkspaceRef token=${JSON.stringify(code.trim())} />`;
    return seg;
  }).join("");
}

type CompileState =
  | { status: "loading" }
  | { status: "ok"; Content: import("mdx/types").MDXContent }
  | { status: "fallback"; error: string };

// FRONTMATTER IS STILL STRIPPED FROM THE BODY — it is metadata for the agent, never body copy —
// but it is no longer DISCARDED: `splitFrontmatter` (./policyDoc) hands back both halves. One file
// needs the block itself. `_global/POLICIES.md` declares `kind: policies`, and there the front
// matter IS the content — it is what this deployment answers, and the prose under it is the
// argument for each answer. Throwing it away would render the reasoning for a set of choices
// without ever showing the choices.

/** THE WIDGET A DOC DECLARES, resolved through the tab REGISTRY rather than imported.
 *
 *  Same seam, and the same reason, as `PagesPanel`'s meeting branch: surfaces REGISTER, renderers
 *  render what is registered. Importing `../canvas/TranscriptWidget` here would drag the whole
 *  meeting source layer — providers, subscriptions, the platform container — into every module that
 *  renders a paragraph of markdown, including the ui-kit's own tests.
 *
 *  A build with nothing registered says so in one line. It does NOT fall back to the raw marker:
 *  the reader would then meet `<!-- vexa:transcript meeting=147 -->` as prose, which is exactly the
 *  defect #1590 removed one file away. */
function TranscriptSlot({ meeting }: { meeting: string }): ReactNode {
  const Widget = registry.tabComponent(TRANSCRIPT_WIDGET_KIND);
  if (!Widget) {
    return (
      <div data-transcript-slot={meeting} style={{ ...SLOT_BOX, color: "var(--t3)", fontSize: 12 }}>
        The live transcript is not available in this build.
      </div>
    );
  }
  return (
    <div data-transcript-slot={meeting} style={SLOT_BOX}>
      <Widget id={`${TRANSCRIPT_WIDGET_KIND}:${meeting}`} params={{ meetingId: meeting }} active />
    </div>
  );
}

/** The widget's own box inside the page: framed, so the reader can see where the document stops and
 *  the room starts, and NOT independently scrollable — it flows with the doc, because the founder's
 *  shape is one page, not a pane inside a pane. */
const SLOT_BOX: CSSProperties = {
  border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)",
  padding: "10px 12px", margin: "10px 0 14px",
};

/** Workspace markdown → the page. A document that declares a transcript slot renders as its parts
 *  with the live transcript in place; every other document takes the single-segment path, which is
 *  the identical render it had before the slot existed. */
export function MdxDoc({ children, style }: { children: string; style?: CSSProperties }): ReactNode {
  // FRONTMATTER FIRST, THEN THE SPLIT, THEN COMMENTS. The slot marker IS an HTML comment, so
  // stripping comments before splitting would drop the widget as machinery — `stripHtmlComments`
  // therefore moved down into `MdxBody`, which sees only text segments.
  const { attrs, body } = splitFrontmatter(children ?? "");
  const segments = splitTranscriptSlots(body);
  // THE ONE PAGE WHOSE FRONT MATTER IS THE POINT (Vexa-ai/vexa#1615). Recognised by what it
  // DECLARES, never by its path: a page is not the policy page because somebody named the file
  // right. The rules render above the prose that argues for them.
  const rules = declaredKind(attrs) === POLICY_KIND
    ? <PolicyRules attrs={attrs} body={body} />
    : null;
  if (!rules && segments.length === 1 && segments[0].kind === "text") {
    return <MdxBody style={style}>{segments[0].text}</MdxBody>;
  }
  return (
    <div style={{ color: "var(--t1)", ...style }}>
      {rules}
      {segments.map((seg, i) => (seg.kind === "transcript"
        ? <TranscriptSlot key={`w${i}`} meeting={seg.meeting} />
        : <MdxBody key={`t${i}`}>{seg.text}</MdxBody>))}
    </div>
  );
}

/** One stretch of prose, rendered as MDX with the registry above; falls back to the legacy
 *  <Markdown> renderer (with a subtle notice) when the source doesn't compile. A doc with a
 *  transcript slot has several of these, and each compiles alone — so a malformed paragraph
 *  downgrades ITSELF and the live transcript beside it is untouched. */
function MdxBody({ children, style }: { children: string; style?: CSSProperties }): ReactNode {
  // Comments out BEFORE anything else looks at the source: MDX has no HTML comment, so
  // escapeUnknownTags below would turn `<!-- desk:pinned:start -->` into visible prose, and the
  // plain-Markdown fallback reads this same string (#1590).
  const src = stripHtmlComments(children ?? "");
  const [state, setState] = useState<CompileState>({ status: "loading" });
  // Recognizing a workspace NAME needs the known set, and the transform cannot await. Prime the
  // snapshot once and recompile when it lands; a warm snapshot costs no second compile.
  const [wsGen, setWsGen] = useState(0);
  useEffect(() => {
    if (knownWorkspaces().length) return;
    let cancelled = false;
    void primeKnownWorkspaces().then(() => { if (!cancelled) setWsGen((n) => n + 1); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    evaluate(transformDocRefs(src), { ...runtime, remarkPlugins: [remarkGfm, remarkForbidExecutable] })
      .then((mod) => { if (!cancelled) setState({ status: "ok", Content: mod.default }); })
      .catch((err: unknown) => { if (!cancelled) setState({ status: "fallback", error: String((err as Error)?.message ?? err) }); });
    return () => { cancelled = true; };
  }, [src, wsGen]);

  if (state.status === "loading") return <div style={{ color: "var(--t3)", fontSize: 12, ...style }}>rendering…</div>;
  if (state.status === "fallback") {
    return (
      <div style={style}>
        {/* fail-loud: name the downgrade AND the reason inline — a tooltip-only error is
            invisible in screenshots and to anyone who doesn't hover */}
        <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--t3)", marginBottom: 8 }}>
          simplified rendering (MDX failed: {state.error})
        </div>
        <Markdown>{src}</Markdown>
      </div>
    );
  }
  const Content = state.Content;
  return (
    <div style={{ color: "var(--t1)", ...style }}>
      <Content components={MDX_COMPONENTS} />
    </div>
  );
}
