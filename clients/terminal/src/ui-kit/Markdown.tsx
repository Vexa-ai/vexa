/** Markdown — a compact, self-contained markdown renderer (no npm dependency).
 *  Parses a markdown string into React nodes, styled with the terminal's dark tokens
 *  (--t1/--t2/--t3, --accent, --blue, --mono, --line, --panel, --panel2). Supports:
 *  headings (#..####), bold, italic, inline code, fenced ```code```, bullet + numbered
 *  lists, links (new tab, rel noreferrer), [[wikilinks]], blockquotes, horizontal rules,
 *  GFM pipe tables, ```mermaid diagrams (./docDiagrams), paragraphs and line breaks.
 *  Intentionally a small subset — robust,
 *  not spec-complete. HTML comments never reach the page — see stripHtmlComments. */
"use client";
import { createContext, Fragment, useContext, type ReactNode } from "react";
import { Card, CardGroup, InternalLink, Wikilink, isInternalHref, useOpenEntity } from "./docLinks";
import { RefChip } from "./RefChip";
// a bare path in a chat names no workspace (2026-10-18) — its own line, beside the renderer imports
import { UnqualifiedPath, useIsUnqualified } from "./docLinks";
import { DocImage } from "./docImages";
import { MeetingChip, meetingNotePath, meetingRefOfHref } from "./MeetingChip";
import { MermaidDiagram, isMermaidFence } from "./docDiagrams";
import { VexaFence } from "./blocks/Blocks";
import { FENCE_LANG } from "./blocks/spec";
import { EntityChip } from "./primitives/Chip";
import { ExternalLink } from "./primitives/Links";
import { CodeGroup, DOC_BLOCKS, TaskMark } from "./docBlocks";
import { directivesToComponents, parseDocComponents, sanitizeDocHtml, taskOf, type DocNode } from "./docComponents";

/** Nodes a host places in the page (the workspace README's live lines): a line `VXSLOT-<n>-VXSLOT`
 *  draws `slots[n]` there. MdxDoc reads the same context, so a page that falls back keeps them. */
export const SlotContext = createContext<ReactNode[]>([]);
const SLOT_LINE = /^VXSLOT-(\d+)-VXSLOT$/;

// A workspace-doc path in inline code → clickable to open the doc. Matches kg/ docs by any
// spelling the agent uses (relative `kg/entities/x.md` or the verbatim absolute mount path
// `<root>/<subject>/kg/...`) plus any doc inside an attached-workspace mount
// (`<root>/.attached/<subject>/<slug>/...md`) — resolveDocRef translates all of them.
// Every workspace doc the agent names is CLICKABLE (founder ruling 2026-08-22: tags all the way,
// chat and pages alike): any `.md` path, any absolute mount path, PURPOSE — one interconnected web.
const ENTITY_PATH = /^(?:\/workspaces\/[\w./ -]+|[\w./ -]*\.md|PURPOSE|[\w./-]*\/[\w./-]+\.(?:md|markdown))$/;
// Clickable `kg/entities/...` inline code — a component so it can read the doc's
// workspace context (DocMeta/DocNav) via useOpenEntity, same as every other link.
function EntityCode({ code }: { code: string }) {
  // A meeting's notes page is named as the meeting, never as its filename (MeetingChip).
  if (meetingNotePath(code)) return <MeetingChip path={code} />;
  return <EntityCodeLink code={code} />;
}
function EntityCodeLink({ code }: { code: string }) {
  const openEntity = useOpenEntity();
  // A bare path with no page around it (a chat reply) names no workspace: plain, never a guess
  // (founder ruling 2026-10-18 — `README.md` opened the organisation's README).
  if (useIsUnqualified(code)) return <UnqualifiedPath text={code} />;
  // A workspace document named in the text is a LINK to it, so it wears the entity chip — not mono
  // blue code that reads as code and behaves as a link (guidelines §4.5). The path is the tooltip;
  // the chip shows the file's name.
  const name = code.split("/").filter(Boolean).pop() ?? code;
  return <EntityChip kind="doc" title={code} onOpen={() => openEntity({ path: code })}>{name}</EntityChip>;
}

// ── HTML comments are machinery, and machinery is not page copy ────────────────────
// The desk README fences its regenerated regions with them — `<!-- desk:pinned:start -->`,
// `<!-- desk:now:end -->`, the pinned hint (core/agent/shared/desk_readme.py) — and this
// renderer printed every one of them as text, because a `<` that starts no known tag is escaped
// into literal prose. Founder, 2026-09-06, walking his own desk: *"not everything is rendered
// correctly here"*. Any markdown viewer shows none of them; the EDIT view, which reads the file
// rather than this renderer, still shows them all.
//
// A FENCE is a transcript of literal text and inline code is the idiom for NAMING a marker, so
// both are copied through untouched — the same rule MdxDoc's transformDocRefs already applies to
// every other rewrite. An UNTERMINATED `<!--` is left as it stands rather than swallowing the
// rest of the document: losing one stray marker is a blemish, losing the page is a failure.
const FENCE_LINE = /^\s*(?:```|~~~)/;

/** Drop every complete `<!-- … -->` from a prose run, fences and inline code excepted. */
function stripProseComments(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    if (text.startsWith("<!--", i)) {
      const end = text.indexOf("-->", i + 4);
      if (end === -1) { out += text.slice(i); break; }   // unterminated: literal, never eat the page
      i = end + 3;                                       // the whole comment, however many lines
      continue;
    }
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end !== -1) { out += text.slice(i, end + 1); i = end + 1; continue; }
    }
    out += text[i];
    i++;
  }
  return out;
}

/** The source a reader is shown: the same markdown, minus its HTML comments. */
export function stripHtmlComments(src: string): string {
  const lines = (src ?? "").replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let prose: string[] = [];
  let fenced = false;
  const flush = () => { if (prose.length) { out.push(stripProseComments(prose.join("\n"))); prose = []; } };
  for (const line of lines) {
    if (FENCE_LINE.test(line)) { flush(); out.push(line); fenced = !fenced; continue; }
    if (fenced) out.push(line); else prose.push(line);
  }
  flush();
  return out.join("\n");
}

// ── inline span parsing: code, bold, italic, links, wikilinks ──────────────────────
// Order matters — `code` is tokenized first so emphasis markers inside it are left literal.
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // split on inline code first; odd indices are code content
  const codeParts = text.split(/(`[^`]+`)/g);
  codeParts.forEach((seg, ci) => {
    if (seg.startsWith("`") && seg.endsWith("`") && seg.length >= 2) {
      const code = seg.slice(1, -1);
      out.push(ENTITY_PATH.test(code)
        ? <EntityCode key={`c${ci}`} code={code} />
        : <code key={`c${ci}`} className="vx-code">{code}</code>,
      );
    } else {
      emphasis(seg, `${ci}`, out);
    }
  });
  return out;
}

/** One line of inline markdown (code, emphasis, links, chips) — for a host that draws its own lines. */
export function InlineMarkdown({ text }: { text: string }): ReactNode {
  return <>{inline(text)}</>;
}

// bold / italic / images / links / wikilinks within a non-code segment
function emphasis(text: string, key: string, out: ReactNode[]): void {
  // `!` BEFORE THE BRACKET IS THE WHOLE DIFFERENCE between a link and an image, and this renderer
  // used to eat it: `![logo](assets/x.svg)` matched the LINK arm, so the page printed a stray `!`
  // followed by a clickable "logo" and no picture at all (#1612). The alternation now takes the
  // optional bang with the token, and the arm branches on it.
  const re = /(\[\[[^\]]+\]\])|(!?\[[^\]]*\]\([^)]+\))|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*]+\*|_[^_]+_)|(https?:\/\/[^\s/<>"']+\/r\/(?:workspace|chat|page|entity|meeting|flow|sensor)\/[^\s<>"'#?|\]\[]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<Fragment key={`${key}-t${i}`}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    if (m[1]) {
      // [[wikilink]] — the same typed entity chip MdxDoc renders (shared resolver; a
      // title with no entity doc renders muted + tooltip instead of a dead click)
      out.push(<Wikilink key={`${key}-w${i}`} title={tok.slice(2, -2)} />);
    } else if (m[2]) {
      // ![alt](src) — the SAME image component MdxDoc registers, so a doc that failed to compile as
      // MDX shows the same picture and offers the same fetch (shared resolver, one behaviour).
      const im = tok.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
      if (im) { out.push(<DocImage key={`${key}-img${i}`} src={im[2]} alt={im[1]} />); last = re.lastIndex; i++; continue; }
      // [text](url) — workspace-internal (schemeless) hrefs navigate in place, resolving
      // relative paths against the linking doc; external links open a browser tab
      const lm = tok.match(/^\[([^\]]*)\]\(([^)]+)\)$/)!;
      const meetingRef = meetingRefOfHref(lm[2]);
      if (meetingRef) { out.push(<MeetingChip key={`${key}-m${i}`} meetingRef={meetingRef} label={lm[1] || undefined} />); last = re.lastIndex; i++; continue; }
      out.push(isInternalHref(lm[2])
        ? <InternalLink key={`${key}-l${i}`} href={lm[2]}>{lm[1] || lm[2]}</InternalLink>
        // external: the one ExternalLink (http/https only, noopener noreferrer); an in-page
        // anchor stays a plain link; any other scheme renders as text
        : lm[2].startsWith("#")
          ? <a key={`${key}-l${i}`} className="vx-link" href={lm[2]}>{lm[1] || lm[2]}</a>
          : <ExternalLink key={`${key}-l${i}`} href={lm[2]}>{lm[1] || lm[2]}</ExternalLink>,
      );
    } else if (m[3]) {
      // **bold** / __bold__ — recurse so **[[wikilink]]** renders the chip, not literal brackets
      const inner: ReactNode[] = [];
      emphasis(tok.slice(2, -2), `${key}-b${i}`, inner);
      out.push(<strong key={`${key}-b${i}`} className="fw-600 c-1">{inner}</strong>);
    } else if (m[5]) {
      // a bare `/r/<kind>/<id>` link (#1783): the same chip a `[[kind:id]]` renders; trailing
      // punctuation stays text
      const trail = /[.,;:!)]+$/.exec(tok)?.[0] ?? "";
      out.push(<RefChip key={`${key}-r${i}`} text={trail ? tok.slice(0, -trail.length) : tok} />);
      if (trail) out.push(<Fragment key={`${key}-rt${i}`}>{trail}</Fragment>);
    } else if (m[4]) {
      // *italic* / _italic_ — recurse for the same reason
      const innerI: ReactNode[] = [];
      emphasis(tok.slice(1, -1), `${key}-i${i}`, innerI);
      out.push(<em key={`${key}-i${i}`} style={{ fontStyle: "italic" }}>{innerI}</em>);
    }
    last = re.lastIndex;
    i++;
  }
  if (last < text.length) out.push(<Fragment key={`${key}-t${i}`}>{text.slice(last)}</Fragment>);
}

// ── <Card>/<CardGroup> fallback parity ─────────────────────────────────────────────
// This renderer is where docs land when their MDX compile FAILS, and agent-written docs
// use the Mintlify card vocabulary heavily — without this, every failed doc prints
// `<CardGroup cols={2}>` as literal text. Parse just those two tags (string/number
// attributes only, matching MdxDoc's no-expressions rule) and render the SAME Card /
// CardGroup components from ./docLinks.
export interface ParsedCard { title?: string; icon?: string; href?: string; body: string }
export interface ParsedCardBlock { cols: number; grouped: boolean; cards: ParsedCard[] }
const CARD_BLOCK_START = /^\s*<Card(Group)?\b/;
function parseCardAttrs(attrs: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of attrs.matchAll(/(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*(\d+)\s*\})/g))
    out[m[1]] = m[2] ?? m[3] ?? m[4];
  return out;
}
/** Parse one card block's source (a <CardGroup>…</CardGroup> or bare <Card>s). Exported for tests. */
export function parseCardBlock(src: string): ParsedCardBlock {
  const group = src.match(/<CardGroup\b([^>]*)>/);
  const cols = group ? Number(parseCardAttrs(group[1]).cols) || 2 : 2;
  const cards: ParsedCard[] = [];
  for (const m of src.matchAll(/<Card\b([^>]*?)(\/>|>([\s\S]*?)<\/Card>)/g)) {
    const a = parseCardAttrs(m[1]);
    cards.push({ title: a.title, icon: a.icon, href: a.href, body: (m[3] ?? "").trim() });
  }
  return { cols, grouped: Boolean(group), cards };
}

const HEADING_SIZE: Record<number, number> = { 1: 18, 2: 16, 3: 14.5, 4: 13.5 };
type TableAlign = "left" | "center" | "right";

function splitTableRow(line: string): string[] | null {
  let body = line.trim();
  if (!body.includes("|")) return null;
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|")) body = body.slice(0, -1);

  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === "\\" && body[i + 1] === "|") {
      cell += "|";
      i++;
    } else if (ch === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += ch;
    }
  }
  cells.push(cell.trim());

  return cells.length >= 2 ? cells : null;
}

function parseTableSeparator(line: string): TableAlign[] | null {
  const cells = splitTableRow(line);
  if (!cells) return null;

  const aligns: TableAlign[] = [];
  for (const cell of cells) {
    const marker = cell.replace(/\s+/g, "");
    if (!/^:?-{3,}:?$/.test(marker)) return null;
    const left = marker.startsWith(":");
    const right = marker.endsWith(":");
    aligns.push(left && right ? "center" : right ? "right" : "left");
  }
  return aligns;
}

function tableStart(lines: string[], index: number): { header: string[]; align: TableAlign[] } | null {
  if (index + 1 >= lines.length) return null;
  const header = splitTableRow(lines[index]);
  const align = parseTableSeparator(lines[index + 1]);
  if (!header || !align) return null;

  const columns = Math.max(header.length, align.length);
  return {
    header: Array.from({ length: columns }, (_, col) => header[col] ?? ""),
    align: Array.from({ length: columns }, (_, col) => align[col] ?? "left"),
  };
}

// ── block parser: split lines into headings, lists, code fences, quotes, rules, paras ──
export function Markdown({ children, style }: { children: string; style?: React.CSSProperties }): ReactNode {
  const slots = useContext(SlotContext);
  // comments out, raw HTML loses its executable parts, `:::` blocks become components; then the
  // components (Mintlify's closed set) become a tree and every stretch of text between them is
  // markdown again — so `<Steps>`, `<Warning>` and friends never print as tags (2026-10-18)
  const src = directivesToComponents(sanitizeDocHtml(stripHtmlComments(children ?? "")));
  return <div className="vx-prose" style={style}>{renderNodes(parseDocComponents(src), slots, "n")}</div>;
}

function renderNodes(nodes: DocNode[], slots: ReactNode[], key: string): ReactNode[] {
  return nodes.map((n, i) => (n.t === "text"
    ? <Fragment key={`${key}${i}`}>{markdownBlocks(n.text, slots)}</Fragment>
    : <Fragment key={`${key}${i}`}>{renderComponent(n, slots, `${key}${i}.`)}</Fragment>));
}

/** One component of the closed set, its children rendered as markdown again. */
function renderComponent(n: Extract<DocNode, { t: "el" }>, slots: ReactNode[], key: string): ReactNode {
  const a = n.attrs;
  const kids = () => renderNodes(n.children, slots, key);
  // containers whose children are items: only the element children count, text between them is layout
  const items = () => n.children.filter((c) => c.t === "el" || c.text.trim()).map((c, i) =>
    c.t === "el" ? <Fragment key={i}>{renderComponent(c, slots, `${key}${i}.`)}</Fragment> : <Fragment key={i}>{markdownBlocks(c.text, slots)}</Fragment>);
  switch (n.name) {
    case "Card": return <Card title={a.title} icon={a.icon} href={a.href}>{n.children.some((c) => c.t === "el" || c.text.trim()) ? kids() : undefined}</Card>;
    case "CardGroup": return <CardGroup cols={Number(a.cols) || 2}>{items()}</CardGroup>;
    case "Steps": return <DOC_BLOCKS.Steps>{items()}</DOC_BLOCKS.Steps>;
    case "Tabs": return <DOC_BLOCKS.Tabs>{n.children.filter((c) => c.t === "el").map((c, i) =>
      <DOC_BLOCKS.Tab key={i} title={c.t === "el" ? c.attrs.title : undefined}>{c.t === "el" ? renderNodes(c.children, slots, `${key}${i}.`) : null}</DOC_BLOCKS.Tab>)}</DOC_BLOCKS.Tabs>;
    case "CodeGroup": {
      // each fence is one tab, named by the text after its language (`bash title`) or the language
      const fences = [...nodeText(n.children).matchAll(/^\s*```([^\n]*)\n([\s\S]*?)^\s*```\s*$/gm)];
      return <CodeGroup items={fences.map((f, i) => {
        const [lang, ...rest] = f[1].trim().split(/\s+/);
        return { label: rest.join(" ") || lang || `Code ${i + 1}`, node: <Fragment key={i}>{markdownBlocks("```" + f[1] + "\n" + f[2] + "```", slots)}</Fragment> };
      })} />;
    }
    case "Columns": {
      // a column whose live list rendered nothing for this reader leaves no gap; none left, no grid
      const cols = n.children.filter((c): c is Extract<DocNode, { t: "el" }> => c.t === "el" && !isBlank(c.children));
      if (!cols.length) return null;
      return <DOC_BLOCKS.Columns cols={a.cols}>{cols.map((c, i) => <Fragment key={i}>{renderComponent(c, slots, `${key}${i}.`)}</Fragment>)}</DOC_BLOCKS.Columns>;
    }
    case "Accordion": return <DOC_BLOCKS.Accordion title={a.title} defaultOpen={a.defaultOpen}>{kids()}</DOC_BLOCKS.Accordion>;
    case "Frame": return <DOC_BLOCKS.Frame caption={a.caption}>{kids()}</DOC_BLOCKS.Frame>;
    case "Step": case "Tab": case "Note": case "Info": case "Warning": case "Tip": case "Check": case "Danger": case "Callout": case "AccordionGroup": case "Column": {
      const C = DOC_BLOCKS[n.name] as (p: { title?: string; children?: ReactNode }) => ReactNode;
      return <C title={a.title}>{kids()}</C>;
    }
  }
}
const isBlank = (nodes: DocNode[]): boolean => nodes.every((c) => c.t === "text" && !c.text.trim());
const nodeText = (nodes: DocNode[]): string => nodes.map((c) => (c.t === "text" ? c.text : nodeText(c.children))).join("");

/** Markdown with no components in it → blocks. */
function markdownBlocks(text: string, slots: ReactNode[]): ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  const flushList = (items: string[], ordered: boolean) => {
    const Tag = ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={key++}>
        {items.map((it, j) => {
          // a GFM task (`- [x] done`) is a box, not literal brackets — read-only here: this renderer
          // has no page file behind it (MdxDoc's Task is the one that can tick a box off)
          const task = taskOf(it);
          return task
            ? <li key={j} className="vx-task-item" data-done={task.done ? "" : undefined}><TaskMark done={task.done} label={task.text} /><span className="vx-task-text">{inline(task.text)}</span></li>
            : <li key={j}>{inline(it)}</li>;
        })}
      </Tag>,
    );
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block ```
    const fence = line.match(/^\s*```(.*)$/);
    if (fence) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      const closed = i < lines.length;   // a fence still being typed/streamed has no closing line yet
      i++; // closing fence
      // ```mermaid → the picture, same component MdxDoc renders (#1617). Only once the fence has
      // CLOSED: this renderer draws the turn that is still streaming into the chat, and half a
      // flowchart is a parse error the reader would watch flash past for no reason.
      // ```vexa → blocks.v1, drawn by the one blocks renderer (#1783): an agent may answer with
      // blocks instead of a wall of text. Same rule as mermaid: only once the fence has closed.
      if (closed && fence[1].trim().toLowerCase() === FENCE_LANG) {
        blocks.push(<VexaFence key={key++} source={buf.join("\n")} />);
        continue;
      }
      if (closed && isMermaidFence(fence[1])) {
        blocks.push(<MermaidDiagram key={key++} source={buf.join("\n")} />);
        continue;
      }
      blocks.push(
        <pre key={key++} className="f-mono t-xs bg-3 bd r-md pt-2 pr-3 pb-2 pl-3 mt-1_5 mr-0 mb-2 ml-0 lh-snug c-1" style={{ overflowX: "auto" }}>
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // blank line
    if (/^\s*$/.test(line)) { i++; continue; }

    // a node the host placed here (SlotContext)
    const slot = SLOT_LINE.exec(line.trim());
    if (slot) { blocks.push(<Fragment key={key++}>{slots[Number(slot[1])] ?? null}</Fragment>); i++; continue; }

    // horizontal rule
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push(<hr key={key++} className="bd-none bd-t mt-3 mr-0 mb-3 ml-0" />);
      i++; continue;
    }

    // heading
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      blocks.push(
        <div key={key++} className="fw-600 c-1 lh-tight" style={{ fontSize: HEADING_SIZE[lvl], margin: lvl <= 2 ? "12px 0 6px" : "10px 0 4px" }}>
          {inline(h[2])}
        </div>,
      );
      i++; continue;
    }

    // blockquote (consume consecutive > lines)
    if (/^\s*>/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      blocks.push(
        <blockquote key={key++}>
          {inline(buf.join("\n"))}
        </blockquote>,
      );
      continue;
    }

    // bullet list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++; }
      flushList(items, false);
      continue;
    }

    // numbered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+[.)]\s+/, "")); i++; }
      flushList(items, true);
      continue;
    }

    // <Card> / <CardGroup> block — same card UI MdxDoc renders (fallback parity)
    if (CARD_BLOCK_START.test(line)) {
      const isGroup = /^\s*<CardGroup\b/.test(line);
      const closeRe = isGroup ? /<\/CardGroup>/ : /(\/>\s*$|<\/Card>)/;
      const buf: string[] = [lines[i]];
      i++;
      while (i < lines.length && !closeRe.test(buf[buf.length - 1])) { buf.push(lines[i]); i++; }
      const parsed = parseCardBlock(buf.join("\n"));
      if (parsed.cards.length === 0) {
        // not actually card markup (e.g. a lone unclosed tag) — show it as literal text
        blocks.push(<p key={key++} className="mt-0 mr-0 mb-2 ml-0 lh-normal">{inline(buf.join(" "))}</p>);
        continue;
      }
      const rendered = parsed.cards.map((c, j) => (
        <Card key={j} title={c.title} icon={c.icon} href={c.href}>{c.body ? inline(c.body.replace(/\s*\n\s*/g, " ")) : undefined}</Card>
      ));
      blocks.push(parsed.grouped
        ? <CardGroup key={key++} cols={parsed.cols}>{rendered}</CardGroup>
        : <div key={key++} className="mt-2 mr-0 mb-3 ml-0" style={{ display: "flex", flexDirection: "column", gap: 10 }}>{rendered}</div>);
      continue;
    }

    // GFM pipe table
    const table = tableStart(lines, i);
    if (table) {
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length) {
        const row = splitTableRow(lines[i]);
        if (!row) break;
        rows.push(Array.from({ length: table.header.length }, (_, col) => row[col] ?? ""));
        i++;
      }
      blocks.push(
        <table key={key++} className="mt-1_5 mr-0 mb-2 ml-0 c-1 lh-snug" style={{ width: "100%", borderCollapse: "collapse", fontSize: "inherit" }}>
          <thead>
            <tr>
              {table.header.map((cell, col) => (
                <th key={col} className="bg-2 bd-strong pt-1_5 pr-2 pb-1_5 pl-2 c-1 fw-600" style={{ textAlign: table.align[col] }}>
                  {inline(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, col) => (
                  <td key={col} className="bd pt-1_5 pr-2 pb-1_5 pl-2 c-2" style={{ textAlign: table.align[col], verticalAlign: "top" }}>
                    {inline(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }

    // paragraph — gather consecutive plain lines until a blank or a block starter
    const para: string[] = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^\s*```/.test(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*>/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i]) && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i]) && !CARD_BLOCK_START.test(lines[i]) && !SLOT_LINE.test(lines[i].trim()) && !tableStart(lines, i)) {
      para.push(lines[i]); i++;
    }
    blocks.push(
      <p key={key++} className="mt-0 mr-0 mb-2 ml-0 lh-normal">
        {para.map((pl, j) => <Fragment key={j}>{j > 0 && <br />}{inline(pl)}</Fragment>)}
      </p>,
    );
  }

  return blocks;
}
