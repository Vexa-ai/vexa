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
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import * as runtime from "react/jsx-runtime";
import { evaluate } from "@mdx-js/mdx";
import remarkGfm from "remark-gfm";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";
import { Markdown } from "./Markdown";
import { Icon } from "./index";

function openEntity(detail: { path?: string; wikilink?: string }): void {
  // beside: links clicked INSIDE a doc must never replace the doc being read — the
  // workbench opens the target in a split group next to it.
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { ...detail, beside: true } }));
}

// ── component registry (closed vocabulary — mirrors Mintlify tag names) ─────────

// Entity-type → chip style (mirrors the TYPE map in surfaces/entities.tsx). Unknown or
// unresolvable types (e.g. /mdx-demo with no gateway) fall back to the neutral blue chip.
export const ENTITY_CHIP: Record<string, { icon: string; color: string; bg: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)" },
  organization: { icon: "web", color: "var(--violet)", bg: "var(--violetbg)" },
  project: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
  meeting: { icon: "cal", color: "var(--violet)", bg: "var(--violetbg)" },
  task: { icon: "tasks", color: "var(--green)", bg: "var(--greenbg)" },
};
export const DEFAULT_ENTITY_CHIP = { icon: "link", color: "var(--blue)", bg: "var(--bluebg)" };

// title → entity type, resolved once per session from the workspace tree
// (slugified title matched against kg/entities/<type>/<slug>.md).
const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
let typeMapPromise: Promise<Map<string, string>> | null = null;
function entityTypes(): Promise<Map<string, string>> {
  typeMapPromise ??= import("../surfaces/workspaceApi")
    .then((api) => api.listWorkspaceTree())
    .then((paths) => {
      const map = new Map<string, string>();
      for (const p of paths ?? []) {
        const m = p.match(/(?:^|\/)kg\/entities\/([^/]+)\/([^/]+)\.md$/);
        if (m && m[2] !== "index") map.set(m[2], m[1]);
      }
      return map;
    })
    .catch(() => new Map<string, string>());
  return typeMapPromise;
}

/** Rich entity chip for [[wikilinks]] — typed pill (icon + color per entity type). */
function Wikilink({ title }: { title: string }) {
  const [hover, setHover] = useState(false);
  const [type, setType] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    void entityTypes().then((m) => { if (on) setType(m.get(slugify(title)) ?? null); });
    return () => { on = false; };
  }, [title]);
  const c = (type && ENTITY_CHIP[type]) || DEFAULT_ENTITY_CHIP;
  return (
    <span onClick={() => openEntity({ wikilink: title })}
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

function Card({ title, icon, href, children }: { title?: string; icon?: string; href?: string; children?: ReactNode }) {
  const [hover, setHover] = useState(false);
  const clickable = Boolean(href);
  const open = () => {
    if (!href) return;
    // scheme allowlist: http(s) opens externally, scheme-less opens in-workspace,
    // anything else (javascript:, data:, //host) is untrusted-doc content — ignore
    if (/^https?:/i.test(href)) window.open(href, "_blank", "noreferrer");
    else if (!/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith("//")) openEntity({ path: href.replace(/^\.\//, "") });
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

function CardGroup({ cols = 2, children }: { cols?: number; children?: ReactNode }) {
  return <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 10, margin: "8px 0 12px" }}>{children}</div>;
}

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
const HEADING_SIZE: Record<number, number> = { 1: 18, 2: 16, 3: 14.5, 4: 13.5 };
const h = (lvl: number) => ({ children }: { children?: ReactNode }) => (
  <div style={{ fontSize: HEADING_SIZE[lvl], fontWeight: 600, color: "var(--t1)", lineHeight: 1.3, margin: lvl <= 2 ? "12px 0 6px" : "10px 0 4px" }}>{children}</div>
);

const htmlComponents = {
  h1: h(1), h2: h(2), h3: h(3), h4: h(4),
  p: ({ children }: { children?: ReactNode }) => <p style={{ margin: "0 0 8px", lineHeight: 1.6 }}>{children}</p>,
  a: ({ href, children }: { href?: string; children?: ReactNode }) => {
    // Workspace-internal link (no scheme, not an anchor) → open the file as a NEW pinned doc tab,
    // same OPEN_ENTITY_EVENT path the Wikilink chip uses. External links open a browser tab.
    const internal = Boolean(href) && !/^[a-z][a-z0-9+.-]*:/i.test(href!) && !href!.startsWith("#") && !href!.startsWith("//");
    if (internal) {
      const path = href!.replace(/^\.\//, "");
      return (
        <span role="link" onClick={() => openEntity({ path })}
          style={{ color: "var(--blue)", textDecoration: "underline", cursor: "pointer" }}>{children}</span>
      );
    }
    // external: only http(s) and #anchors keep a live href — javascript:/data:/;
    // //host from untrusted docs render as inert text
    const safeHref = href && (/^https?:/i.test(href) || href.startsWith("#")) ? href : undefined;
    if (!safeHref) return <span style={{ color: "var(--blue)" }}>{children}</span>;
    return <a href={safeHref} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "underline" }}>{children}</a>;
  },
  code: ({ children }: { children?: ReactNode }) => (
    <code style={{ fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px", color: "var(--t1)" }}>{children}</code>
  ),
  pre: ({ children }: { children?: ReactNode }) => (
    <pre style={{ fontFamily: "var(--mono)", fontSize: 12, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", margin: "6px 0 10px", overflowX: "auto", lineHeight: 1.5, color: "var(--t1)" }}>{children}</pre>
  ),
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

export const MDX_COMPONENTS = { ...htmlComponents, Note, Warning, Card, CardGroup, Steps, Step, Tabs, Tab, Wikilink };

// ── security: forbid executable MDX ──────────────────────────────────────────
// kg/ markdown is agent-written from meeting transcripts and external content, so it
// is untrusted input. The component registry closes which TAGS resolve, but MDX
// expressions (`{...}`), ESM (`import`/`export`), and expression-valued attributes
// are arbitrary JS run in the viewer's session — reject them at the syntax tree and
// let the throw route into the plain-Markdown fallback below.
const FORBIDDEN_MDX_NODES = new Set(["mdxjsEsm", "mdxFlowExpression", "mdxTextExpression"]);
function assertNoExecutableMdx(node: { type?: string; attributes?: unknown[]; children?: unknown[] }): void {
  if (node.type && FORBIDDEN_MDX_NODES.has(node.type)) throw new Error(`executable MDX (${node.type}) is not allowed in workspace docs`);
  for (const attr of (node.attributes ?? []) as { type?: string; value?: { type?: string } }[]) {
    if (attr?.type === "mdxJsxExpressionAttribute" || attr?.value?.type === "mdxJsxAttributeValueExpression")
      throw new Error("expression-valued JSX attributes are not allowed in workspace docs");
  }
  for (const child of (node.children ?? []) as { type?: string }[]) assertNoExecutableMdx(child);
}
function remarkForbidExecutable() {
  return (tree: { type?: string; children?: unknown[] }) => assertNoExecutableMdx(tree);
}

// ── wikilink preprocessing: [[Title]] → <Wikilink title="Title" /> (skip code) ───
function transformWikilinks(src: string): string {
  // split out fenced code blocks and inline code; only rewrite prose segments
  return src.split(/(```[\s\S]*?```|`[^`]*`)/g).map((seg, i) =>
    i % 2 === 1 ? seg : seg.replace(/\[\[([^\]]+)\]\]/g, (_m, t: string) => `<Wikilink title=${JSON.stringify(t)} />`),
  ).join("");
}

type CompileState =
  | { status: "loading" }
  | { status: "ok"; Content: import("mdx/types").MDXContent }
  | { status: "fallback"; error: string };

/** Renders workspace markdown as MDX with the registry above; falls back to the
 *  legacy <Markdown> renderer (with a subtle notice) when the source doesn't compile. */
export function MdxDoc({ children, style }: { children: string; style?: CSSProperties }): ReactNode {
  const src = children ?? "";
  const [state, setState] = useState<CompileState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    evaluate(transformWikilinks(src), { ...runtime, remarkPlugins: [remarkGfm, remarkForbidExecutable] })
      .then((mod) => { if (!cancelled) setState({ status: "ok", Content: mod.default }); })
      .catch((err: unknown) => { if (!cancelled) setState({ status: "fallback", error: String((err as Error)?.message ?? err) }); });
    return () => { cancelled = true; };
  }, [src]);

  if (state.status === "loading") return <div style={{ color: "var(--t3)", fontSize: 12, ...style }}>rendering…</div>;
  if (state.status === "fallback") {
    return (
      <div style={style}>
        <div title={state.error} style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--t3)", marginBottom: 8 }}>plain markdown (MDX parse failed)</div>
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
