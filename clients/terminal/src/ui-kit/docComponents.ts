/** docComponents — the Mintlify dialect agents write pages in, read as DATA (no React here).
 *
 *  Agents write workspace pages the way our docs are written: markdown plus a closed set of
 *  Mintlify components (`<Warning>`, `<Steps>`/`<Step title=…>`, `<CardGroup>`, `<Tabs>`, …), GFM
 *  task lists and the page grammar's `:::columns` / `:::callout` blocks. Founder, 2026-10-18, on
 *  the OeNB pilot page: *"things do not render"* — `<Steps>`, `</Warning>` and `[x]` printed as text.
 *
 *  Three pure passes, shared by both renderers (MdxDoc and the plain-Markdown fallback):
 *    · `sanitizeDocHtml`      — raw HTML loses every executable part (script, iframe, on*=, javascript:);
 *    · `directivesToComponents` — `:::callout` / `:::columns` become the same components;
 *    · `parseDocComponents`   — a component tree for the plain renderer, which has no MDX compiler.
 *  Fenced code is never touched by any of them: a fence is a transcript of literal text. */

/** The components a page may use. Anything else capitalised renders as its inner content. */
export const DOC_COMPONENTS = [
  "Note", "Warning", "Tip", "Info", "Check", "Danger", "Callout",
  "Steps", "Step", "Card", "CardGroup", "Accordion", "AccordionGroup", "Tabs", "Tab",
  "Frame", "CodeGroup", "Columns", "Column",
] as const;
export type DocComponentName = (typeof DOC_COMPONENTS)[number];
const KNOWN = new Set<string>(DOC_COMPONENTS);
export const isDocComponent = (name: string): name is DocComponentName => KNOWN.has(name);

const FENCE_LINE = /^\s*(?:```|~~~)/;

/** Run `fn` over the prose of `src` only; fenced blocks are copied through untouched. */
export function mapProse(src: string, fn: (prose: string) => string): string {
  const lines = (src ?? "").split("\n");
  const out: string[] = [];
  let prose: string[] = [];
  let fenced = false;
  const flush = () => { if (prose.length) { out.push(fn(prose.join("\n"))); prose = []; } };
  for (const line of lines) {
    if (FENCE_LINE.test(line)) { if (!fenced) flush(); out.push(line); fenced = !fenced; continue; }
    if (fenced) out.push(line); else prose.push(line);
  }
  flush();
  return out.join("\n");
}

// ── sanitizing ────────────────────────────────────────────────────────────────────────────────
// Neither renderer ever puts page text into the DOM as HTML (no dangerouslySetInnerHTML), so raw HTML
// cannot execute; this pass makes sure it does not even SHOW: an `<iframe>` or a `<script>` block is
// removed with its content, an `on…=` handler and a `javascript:` URL are dropped from any tag left.
const DANGEROUS = "script|iframe|object|embed|style|link|meta|base|form|frame|frameset|applet|noscript|template|svg|math";
// case-insensitive (`<IFRAME>` is an iframe), except the one component that shares a name: `<Frame>`
const DANGEROUS_BLOCK = new RegExp(String.raw`<\s*(${DANGEROUS})\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>`, "gi");
const DANGEROUS_TAG = new RegExp(String.raw`<\s*\/?\s*(${DANGEROUS})\b[^>]*>?`, "gi");
const unlessComponent = (m: string, name: string) => (name === "Frame" ? m : "");
const TAG = /<[A-Za-z][\w:.-]*(?:\s+[^<>]*?)?\/?>/g;
const ON_ATTR = /\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|\{[^}]*\}|[^\s>]+)/gi;
const BAD_URL = /\b(href|src|xlink:href|action|formaction|poster|background)\s*=\s*(["']?)\s*(?:javascript|vbscript|data):[^"'\s>]*\2/gi;

function sanitizeProse(s: string): string {
  return s
    .replace(DANGEROUS_BLOCK, unlessComponent)
    .replace(DANGEROUS_TAG, unlessComponent)
    .replace(TAG, (tag) => tag.replace(ON_ATTR, "").replace(BAD_URL, '$1="#"'));
}

/** Raw HTML in a page loses every executable part; fenced code and inline code are left as written. */
export function sanitizeDocHtml(md: string): string {
  return mapProse(md, (prose) => prose.split(/(`[^`\n]*`)/g).map((seg, i) => (i % 2 ? seg : sanitizeProse(seg))).join(""));
}

// ── page-grammar blocks → components ─────────────────────────────────────────────────────────
// `:::callout` / `:::columns` (columns split by a `---` line) / `:::stats` are the page grammar's
// own blocks (core/agent/worker/engine.py `page_blocks_preamble`). The workspace page's rich renderer
// draws them; every other renderer printed them. Here they become the components the registry already
// draws, so a page reads the same in the file view, the workspace page and the fallback.
const DIRECTIVE_OPEN = /^\s*:::\s*([A-Za-z][\w-]*)\b(.*)$/;
const DIRECTIVE_CLOSE = /^\s*:::\s*$/;
const COLUMN_SPLIT = /^\s*---+\s*$/;

const attrSafe = (s: string) => s.replace(/["<>{}]/g, "").trim();

export function directivesToComponents(md: string): string {
  return mapProse(md, (prose) => {
    const out: string[] = [];
    const stack: string[] = [];
    for (const line of prose.split("\n")) {
      const open = DIRECTIVE_OPEN.exec(line);
      if (open) {
        const name = open[1].toLowerCase();
        const rest = open[2].trim();
        if (name === "columns") { out.push("", "<Columns>", "<Column>", ""); stack.push("columns"); }
        else if (name === "callout" || name === "note" || name === "info" || name === "tip" || name === "warning") {
          const tag = name === "callout" ? "Note" : name[0].toUpperCase() + name.slice(1);
          const title = /^title\s*=\s*["']?([^"']*)["']?$/.exec(rest)?.[1] ?? (rest && !rest.includes("=") ? rest : "");
          out.push("", title ? `<${tag} title="${attrSafe(title)}">` : `<${tag}>`, "");
          stack.push(tag);
        } else { stack.push(""); }                 // :::stats and the unknown: the marker goes, inner stays
        continue;
      }
      if (DIRECTIVE_CLOSE.test(line) && stack.length) {
        const top = stack.pop()!;
        if (top === "columns") out.push("", "</Column>", "</Columns>", "");
        else if (top) out.push("", `</${top}>`, "");
        continue;
      }
      if (stack[stack.length - 1] === "columns" && COLUMN_SPLIT.test(line)) { out.push("", "</Column>", "<Column>", ""); continue; }
      out.push(line);
    }
    while (stack.length) {                          // an unclosed block closes at the end, never eats the page
      const top = stack.pop()!;
      if (top === "columns") out.push("", "</Column>", "</Columns>", "");
      else if (top) out.push("", `</${top}>`, "");
    }
    return out.join("\n");
  });
}

// ── component tree (for the renderer with no MDX compiler) ───────────────────────────────────
export type DocNode =
  | { t: "text"; text: string }
  | { t: "el"; name: DocComponentName; attrs: Record<string, string>; children: DocNode[] };

/** One capitalised tag: `<Name a="x" b={2} flag>` · `</Name>` · `<Name … />`. */
const COMPONENT_TAG = /<(\/?)([A-Z][A-Za-z0-9]*)((?:\s+[A-Za-z_:][\w:.-]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|\{[^}]*\}))?)*)\s*(\/?)>/g;
const ATTR = /([A-Za-z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*([^}]*?)\s*\}))?/g;
const LITERAL = /^(-?\d+(\.\d+)?|true|false|null|'[^'\\]*'|"[^"\\]*")$/;

/** A tag's attributes: strings as written, literal expressions (`cols={2}`) as their text, a bare
 *  attribute as "true"; an expression that is not a literal is DROPPED, never evaluated. */
export function parseAttrs(src: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of (src ?? "").matchAll(ATTR)) {
    if (/^on/i.test(m[1])) continue;
    if (m[2] !== undefined || m[3] !== undefined) out[m[1]] = m[2] ?? m[3];
    else if (m[4] !== undefined) { if (LITERAL.test(m[4])) out[m[1]] = m[4].replace(/^['"]|['"]$/g, ""); }
    else out[m[1]] = "true";
  }
  return out;
}

/** The label an unknown component keeps when it drops away (`<ParamField path="limit">`). */
export function unknownLabel(attrs: Record<string, string>): string {
  return (attrs.title ?? attrs.name ?? attrs.path ?? attrs.query ?? attrs.body ?? attrs.header ?? attrs.label ?? "").trim();
}

type Tok = { t: "text"; text: string } | { t: "open"; name: string; attrs: Record<string, string>; self: boolean } | { t: "close"; name: string };

function tokenize(src: string): Tok[] {
  const toks: Tok[] = [];
  const pushText = (s: string) => {
    if (!s) return;
    const last = toks[toks.length - 1];
    if (last && last.t === "text") last.text += s; else toks.push({ t: "text", text: s });
  };
  const lines = src.split("\n");
  let fenced = false;
  let buf: string[] = [];
  const flushProse = () => {
    if (!buf.length) return;
    const prose = buf.join("\n") + "\n";
    buf = [];
    // inline code is literal too: only the stretches outside backticks are scanned for tags
    prose.split(/(`[^`\n]*`)/g).forEach((seg, i) => {
      if (i % 2) { pushText(seg); return; }
      let last = 0;
      for (const m of seg.matchAll(COMPONENT_TAG)) {
        pushText(seg.slice(last, m.index));
        last = (m.index ?? 0) + m[0].length;
        if (m[1]) toks.push({ t: "close", name: m[2] });
        else toks.push({ t: "open", name: m[2], attrs: parseAttrs(m[3]), self: !!m[4] });
        if (seg[last] === "\n") { pushText("\n"); last++; }
      }
      pushText(seg.slice(last));
    });
  };
  for (const line of lines) {
    if (FENCE_LINE.test(line)) {
      if (!fenced) flushProse();
      pushText(line + "\n");
      fenced = !fenced;
      continue;
    }
    if (fenced) pushText(line + "\n"); else buf.push(line);
  }
  flushProse();
  // the trailing newline the line split added
  const last = toks[toks.length - 1];
  if (last && last.t === "text" && last.text.endsWith("\n") && !src.endsWith("\n")) last.text = last.text.slice(0, -1);
  return toks;
}

/** Markdown with components → a tree. Known components nest; an unknown one is TRANSPARENT (its
 *  children stay, its title becomes a bold line); a stray closing tag is dropped; anything left open
 *  closes at the end of the page. */
export function parseDocComponents(src: string): DocNode[] {
  type Frame = { name: string; known: boolean; attrs: Record<string, string>; children: DocNode[] };
  const root: Frame = { name: "", known: false, attrs: {}, children: [] };
  const stack: Frame[] = [root];
  const add = (n: DocNode) => {
    const kids = stack[stack.length - 1].children;
    const last = kids[kids.length - 1];
    if (n.t === "text" && last && last.t === "text") last.text += n.text; else kids.push(n);
  };
  const close = (f: Frame) => {
    if (f.known) add({ t: "el", name: f.name as DocComponentName, attrs: f.attrs, children: f.children });
    else for (const c of f.children) add(c);
  };
  for (const tok of tokenize(src ?? "")) {
    if (tok.t === "text") { add(tok); continue; }
    if (tok.t === "open") {
      const known = isDocComponent(tok.name);
      if (!known) {
        const label = unknownLabel(tok.attrs);
        if (label) add({ t: "text", text: `\n**${label}**\n\n` });
        if (tok.self) continue;
      }
      const frame: Frame = { name: tok.name, known, attrs: tok.attrs, children: [] };
      if (tok.self) { stack.push(frame); stack.pop(); close(frame); continue; }
      stack.push(frame);
      continue;
    }
    // a close: unwind to the matching open; a close with no open is dropped
    const at = stack.map((f) => f.name).lastIndexOf(tok.name);
    if (at <= 0) continue;
    while (stack.length > at) close(stack.pop()!);
  }
  while (stack.length > 1) close(stack.pop()!);
  return root.children;
}

// ── task lists ───────────────────────────────────────────────────────────────────────────────
/** `[ ] text` / `[x] text` at the start of a list item → its state and the rest of the text. */
export const TASK_ITEM = /^\[( |x|X)\]\s+(.*)$/s;
export function taskOf(item: string): { done: boolean; text: string } | null {
  const m = TASK_ITEM.exec(item);
  return m ? { done: m[1] !== " ", text: m[2] } : null;
}
