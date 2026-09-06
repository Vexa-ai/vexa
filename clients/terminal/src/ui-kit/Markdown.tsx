/** Markdown — a compact, self-contained markdown renderer (no npm dependency).
 *  Parses a markdown string into React nodes, styled with the terminal's dark tokens
 *  (--t1/--t2/--t3, --accent, --blue, --mono, --line, --panel, --panel2). Supports:
 *  headings (#..####), bold, italic, inline code, fenced ```code```, bullet + numbered
 *  lists, links (new tab, rel noreferrer), [[wikilinks]], blockquotes, horizontal rules,
 *  GFM pipe tables, ```mermaid diagrams (./docDiagrams), paragraphs and line breaks.
 *  Intentionally a small subset — robust,
 *  not spec-complete. HTML comments never reach the page — see stripHtmlComments. */
"use client";
import { Fragment, type ReactNode } from "react";
import { Card, CardGroup, InternalLink, Wikilink, isInternalHref, useOpenEntity } from "./docLinks";
import { DocImage } from "./docImages";
import { MermaidDiagram, isMermaidFence } from "./docDiagrams";

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
  const openEntity = useOpenEntity();
  return (
    <code onClick={() => openEntity({ path: code })}
      style={{ fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px", color: "var(--blue)", cursor: "pointer" }}>
      {code}
    </code>
  );
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
        : <code key={`c${ci}`}
            style={{ fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px", color: "var(--t1)" }}>
            {code}
          </code>,
      );
    } else {
      emphasis(seg, `${ci}`, out);
    }
  });
  return out;
}

// bold / italic / images / links / wikilinks within a non-code segment
function emphasis(text: string, key: string, out: ReactNode[]): void {
  // `!` BEFORE THE BRACKET IS THE WHOLE DIFFERENCE between a link and an image, and this renderer
  // used to eat it: `![logo](assets/x.svg)` matched the LINK arm, so the page printed a stray `!`
  // followed by a clickable "logo" and no picture at all (#1612). The alternation now takes the
  // optional bang with the token, and the arm branches on it.
  const re = /(\[\[[^\]]+\]\])|(!?\[[^\]]*\]\([^)]+\))|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*]+\*|_[^_]+_)/g;
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
      out.push(isInternalHref(lm[2])
        ? <InternalLink key={`${key}-l${i}`} href={lm[2]}>{lm[1] || lm[2]}</InternalLink>
        : <a key={`${key}-l${i}`} href={/^https?:/i.test(lm[2]) || lm[2].startsWith("#") ? lm[2] : undefined} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "underline" }}>
            {lm[1] || lm[2]}
          </a>,
      );
    } else if (m[3]) {
      // **bold** / __bold__ — recurse so **[[wikilink]]** renders the chip, not literal brackets
      const inner: ReactNode[] = [];
      emphasis(tok.slice(2, -2), `${key}-b${i}`, inner);
      out.push(<strong key={`${key}-b${i}`} style={{ fontWeight: 600, color: "var(--t1)" }}>{inner}</strong>);
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
  const src = stripHtmlComments(children ?? "");
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  const flushList = (items: string[], ordered: boolean) => {
    const Tag = ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={key++} style={{ margin: "4px 0 8px", paddingLeft: 20, display: "flex", flexDirection: "column", gap: 2 }}>
        {items.map((it, j) => <li key={j} style={{ lineHeight: 1.55 }}>{inline(it)}</li>)}
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
      if (closed && isMermaidFence(fence[1])) {
        blocks.push(<MermaidDiagram key={key++} source={buf.join("\n")} />);
        continue;
      }
      blocks.push(
        <pre key={key++} style={{ fontFamily: "var(--mono)", fontSize: 12, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px", margin: "6px 0 10px", overflowX: "auto", lineHeight: 1.5, color: "var(--t1)" }}>
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // blank line
    if (/^\s*$/.test(line)) { i++; continue; }

    // horizontal rule
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push(<hr key={key++} style={{ border: "none", borderTop: "1px solid var(--line)", margin: "12px 0" }} />);
      i++; continue;
    }

    // heading
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      blocks.push(
        <div key={key++} style={{ fontSize: HEADING_SIZE[lvl], fontWeight: 600, color: "var(--t1)", lineHeight: 1.3, margin: lvl <= 2 ? "12px 0 6px" : "10px 0 4px" }}>
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
        <blockquote key={key++} style={{ borderLeft: "3px solid var(--line2)", paddingLeft: 12, margin: "6px 0 8px", color: "var(--t2)", lineHeight: 1.55 }}>
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
        blocks.push(<p key={key++} style={{ margin: "0 0 8px", lineHeight: 1.6 }}>{inline(buf.join(" "))}</p>);
        continue;
      }
      const rendered = parsed.cards.map((c, j) => (
        <Card key={j} title={c.title} icon={c.icon} href={c.href}>{c.body ? inline(c.body.replace(/\s*\n\s*/g, " ")) : undefined}</Card>
      ));
      blocks.push(parsed.grouped
        ? <CardGroup key={key++} cols={parsed.cols}>{rendered}</CardGroup>
        : <div key={key++} style={{ display: "flex", flexDirection: "column", gap: 10, margin: "8px 0 12px" }}>{rendered}</div>);
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
        <table key={key++} style={{ width: "100%", borderCollapse: "collapse", margin: "6px 0 10px", color: "var(--t1)", fontSize: "inherit", lineHeight: 1.45 }}>
          <thead>
            <tr>
              {table.header.map((cell, col) => (
                <th key={col} style={{ background: "var(--panel)", border: "1px solid var(--line2)", padding: "6px 9px", textAlign: table.align[col], color: "var(--t1)", fontWeight: 600 }}>
                  {inline(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, col) => (
                  <td key={col} style={{ border: "1px solid var(--line)", padding: "6px 9px", textAlign: table.align[col], color: "var(--t2)", verticalAlign: "top" }}>
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
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^\s*```/.test(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*>/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i]) && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i]) && !CARD_BLOCK_START.test(lines[i]) && !tableStart(lines, i)) {
      para.push(lines[i]); i++;
    }
    blocks.push(
      <p key={key++} style={{ margin: "0 0 8px", lineHeight: 1.6 }}>
        {para.map((pl, j) => <Fragment key={j}>{j > 0 && <br />}{inline(pl)}</Fragment>)}
      </p>,
    );
  }

  return <div style={{ color: "var(--t1)", ...style }}>{blocks}</div>;
}
