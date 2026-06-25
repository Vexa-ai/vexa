/** Markdown — a compact, self-contained markdown renderer (no npm dependency).
 *  Parses a markdown string into React nodes, styled with the terminal's dark tokens
 *  (--t1/--t2/--t3, --accent, --blue, --mono, --line, --panel, --panel2). Supports:
 *  headings (#..####), bold, italic, inline code, fenced ```code```, bullet + numbered
 *  lists, links (new tab, rel noreferrer), [[wikilinks]], blockquotes, horizontal rules,
 *  paragraphs and line breaks. Intentionally a small subset — robust, not spec-complete. */
import { Fragment, type ReactNode } from "react";

// ── inline span parsing: code, bold, italic, links, wikilinks ──────────────────────
// Order matters — `code` is tokenized first so emphasis markers inside it are left literal.
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // split on inline code first; odd indices are code content
  const codeParts = text.split(/(`[^`]+`)/g);
  codeParts.forEach((seg, ci) => {
    if (seg.startsWith("`") && seg.endsWith("`") && seg.length >= 2) {
      out.push(
        <code key={`c${ci}`} style={{ fontFamily: "var(--mono)", fontSize: "0.88em", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0.5px 5px", color: "var(--t1)" }}>
          {seg.slice(1, -1)}
        </code>,
      );
    } else {
      emphasis(seg, `${ci}`, out);
    }
  });
  return out;
}

// bold / italic / links / wikilinks within a non-code segment
function emphasis(text: string, key: string, out: ReactNode[]): void {
  const re = /(\[\[[^\]]+\]\])|(\[[^\]]*\]\([^)]+\))|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*]+\*|_[^_]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<Fragment key={`${key}-t${i}`}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    if (m[1]) {
      // [[wikilink]]
      out.push(<span key={`${key}-w${i}`} style={{ color: "var(--blue)" }}>{tok}</span>);
    } else if (m[2]) {
      // [text](url)
      const lm = tok.match(/^\[([^\]]*)\]\(([^)]+)\)$/)!;
      out.push(
        <a key={`${key}-l${i}`} href={lm[2]} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "underline" }}>
          {lm[1] || lm[2]}
        </a>,
      );
    } else if (m[3]) {
      // **bold** / __bold__
      out.push(<strong key={`${key}-b${i}`} style={{ fontWeight: 600, color: "var(--t1)" }}>{tok.slice(2, -2)}</strong>);
    } else if (m[4]) {
      // *italic* / _italic_
      out.push(<em key={`${key}-i${i}`} style={{ fontStyle: "italic" }}>{tok.slice(1, -1)}</em>);
    }
    last = re.lastIndex;
    i++;
  }
  if (last < text.length) out.push(<Fragment key={`${key}-t${i}`}>{text.slice(last)}</Fragment>);
}

const HEADING_SIZE: Record<number, number> = { 1: 18, 2: 16, 3: 14.5, 4: 13.5 };

// ── block parser: split lines into headings, lists, code fences, quotes, rules, paras ──
export function Markdown({ children, style }: { children: string; style?: React.CSSProperties }): ReactNode {
  const src = children ?? "";
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
      i++; // closing fence
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

    // paragraph — gather consecutive plain lines until a blank or a block starter
    const para: string[] = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^\s*```/.test(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*>/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i]) && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(lines[i])) {
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
