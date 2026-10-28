/** THE RICH PAGE GRAMMAR (#1783, R3, feature `home2`; BUILD 2026-10-16 "Rich README").
 *
 *  A page stays MARKDOWN — git-friendly, written by people and the agent — with a few additions the
 *  renderer draws, so a page can look designed and be live without anyone authoring code:
 *
 *    typed references   [[room:id]] [[chat:id]] [[call:id]] [[file:path]] [[person:id]] [[card:id]]
 *                       [[routine:id]] [[workspace:id]], optionally `|label` (a hint for readers with
 *                       no server; the server's answer, for THIS viewer, is what is drawn)
 *    badges             {{status}} {{needs-you}} {{working-here}} {{members}} {{upcoming}}
 *    blocks             :::columns (--- between columns) · :::callout · :::stats source=<file.json>
 *    actions            [[action:<verb>:<target> | label]] — verbs from ACTION_VERBS only
 *    view blocks        ```view  source: pages|crm.card|meetings|rooms  as: table|board  …
 *
 *  NO CODE IN PAGES: raw HTML is sanitized here before any renderer sees it (and MdxDoc refuses
 *  executable MDX on its own); an action outside the allow-list is its plain label; a view source
 *  outside the list is nothing. Pure, so every rule is provable without a browser. */

import { ACCEPTED_VERBS, VERBS, canonicalVerb } from "../../readme/grammar";

export const REF_KINDS = ["room", "chat", "call", "file", "person", "card", "routine", "workspace"] as const;
export type RefKind = (typeof REF_KINDS)[number];
export const BADGES = ["status", "needs-you", "working-here", "members", "upcoming"] as const;
export type Badge = (typeof BADGES)[number];

/** The allow-list is THE ONE VERB REGISTRY (PRD 0.13.4 D17, `../../readme/grammar.ts`): the old
 *  `[[action:…]]` verbs and the README's `{{verb …}}` actions are one table; old spellings are aliases.
 *  Each runs as the person who clicked, through their own routes. */
export const ACTION_VERBS: readonly string[] = ACCEPTED_VERBS;
export type ActionVerb = string;
/** Acts with an effect beyond the person's own screen (other people see it, mail goes out, a shared
 *  record changes) are confirmed first — the registry says which. */
export const actionNeedsConfirm = (verb: string): boolean => {
  const v = canonicalVerb(verb);
  return v ? VERBS[v].confirm : false;
};

export const VIEW_SOURCES = ["pages", "crm.card", "meetings", "rooms"] as const;
export type ViewSource = (typeof VIEW_SOURCES)[number];
export type ViewSpec = {
  source: ViewSource; as: "table" | "board"; group?: string; columns?: string[]; folder?: string;
  filter?: Record<string, string>; limit: number;
};
const VIEW_LIMIT_MAX = 200;

const KIND_ALT = REF_KINDS.join("|");
const REF_SRC = String.raw`\[\[(${KIND_ALT}):([^\]|]{1,300})(?:\|([^\]]*))?\]\]`;
const BADGE_SRC = String.raw`\{\{(${BADGES.join("|")})\}\}`;
const ACTION_SRC = String.raw`\[\[action:([a-z-]{1,40}):([^\]|]{0,300})\|([^\]]{1,120})\]\]`;

export type InlinePart =
  | { t: "text"; text: string }
  | { t: "ref"; kind: RefKind; id: string; key: string; label?: string }
  | { t: "badge"; badge: Badge }
  | { t: "action"; verb: ActionVerb; target: string; label: string };

export type Block =
  | { t: "md"; text: string }
  | { t: "line"; text: string }
  | { t: "columns"; cols: Block[][] }
  | { t: "callout"; inner: Block[] }
  | { t: "steps"; steps: { title: string; inner: Block[] }[] }
  | { t: "stats"; source: string }
  | { t: "view"; spec: ViewSpec };

// ── sanitizing ────────────────────────────────────────────────────────────────────────────────

const DANGEROUS = "script|iframe|object|embed|style|link|meta|base|form|frame|frameset|applet|noscript|template";

function sanitizeChunk(s: string): string {
  return s
    .replace(new RegExp(String.raw`<\s*(${DANGEROUS})\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>`, "gi"), "")
    .replace(new RegExp(String.raw`<\s*\/?\s*(${DANGEROUS})\b[^>]*>?`, "gi"), "")
    .replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    .replace(/\b(href|src|xlink:href|action|formaction)\s*=\s*("|')?\s*(javascript|vbscript|data):[^"'\s>]*\2?/gi, "$1=\"#\"");
}

/** Raw HTML in a page loses every executable part; fenced code is left as written (it is text). */
export function sanitizeHtml(md: string): string {
  const parts = md.split(/(^```[^\n]*\n[\s\S]*?^```[ \t]*$)/m);
  return parts.map((p, i) => (i % 2 === 1 ? p : sanitizeChunk(p))).join("");
}

export function splitFrontMatter(md: string): { meta: Record<string, string>; body: string } {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(md);
  if (!m) return { meta: {}, body: md };
  const meta: Record<string, string> = {};
  for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { meta, body: md.slice(m[0].length) };
}

// ── references, actions, views ────────────────────────────────────────────────────────────────

/** Fenced code removed (references inside code are text). */
const withoutFences = (md: string) => md.replace(/^```[^\n]*\n[\s\S]*?^```[ \t]*$/gm, "");

export function parseRefs(md: string): { kind: RefKind; id: string; key: string; label?: string }[] {
  const out: { kind: RefKind; id: string; key: string; label?: string }[] = [];
  const seen = new Set<string>();
  for (const m of withoutFences(md).matchAll(new RegExp(REF_SRC, "g"))) {
    const id = m[2].trim();
    if (!id) continue;
    const key = `${m[1]}:${id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const label = (m[3] ?? "").trim();
    out.push({ kind: m[1] as RefKind, id, key, ...(label ? { label } : {}) });
  }
  return out;
}

export function parseAction(text: string): { verb: ActionVerb; target: string; label: string } | null {
  const m = new RegExp(`^${ACTION_SRC}$`).exec(text.trim());
  if (!m || !(ACTION_VERBS as readonly string[]).includes(m[1])) return null;
  return { verb: m[1] as ActionVerb, target: m[2].trim(), label: m[3].trim() };
}

export function parseView(src: string): ViewSpec | null {
  const kv: Record<string, string> = {};
  for (const line of src.split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) kv[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
  }
  const source = kv.source as ViewSource;
  if (!(VIEW_SOURCES as readonly string[]).includes(source)) return null;
  const filter: Record<string, string> = {};
  for (const pair of (kv.filter ?? "").split(",")) {
    const [k, ...v] = pair.split("=");
    if (k?.trim() && v.length) filter[k.trim()] = v.join("=").trim();
  }
  const n = Number.parseInt(kv.limit ?? "", 10);
  return {
    source,
    as: kv.as === "board" ? "board" : "table",
    ...(kv.group ? { group: kv.group } : {}),
    ...(kv.columns ? { columns: kv.columns.split(",").map((c) => c.trim()).filter(Boolean) } : {}),
    ...(Object.keys(filter).length ? { filter } : {}),
    folder: kv.folder || undefined,
    limit: Number.isFinite(n) && n > 0 ? Math.min(n, VIEW_LIMIT_MAX) : 50,
  } as ViewSpec;
}

// ── blocks ────────────────────────────────────────────────────────────────────────────────────

const LIVE_LINE = new RegExp(`${REF_SRC}|${BADGE_SRC}|\\[\\[action:`);

export function parseBlocks(md: string): Block[] {
  const lines = md.split("\n");
  const out: Block[] = [];
  let buf: string[] = [];
  const flush = () => { if (buf.join("").trim()) out.push({ t: "md", text: buf.join("\n") }); buf = []; };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    const fence = /^```\s*(\S*)/.exec(l);
    if (fence) {
      let j = i + 1;
      const inner: string[] = [];
      for (; j < lines.length && !/^```\s*$/.test(lines[j]); j++) inner.push(lines[j]);
      if (fence[1] === "view") {
        flush();
        const spec = parseView(inner.join("\n"));
        if (spec) out.push({ t: "view", spec });
      } else {
        buf.push(...lines.slice(i, Math.min(j + 1, lines.length)));
      }
      i = j;
      continue;
    }
    // <Steps> … </Steps> is ONE block (founder 2026-10-16): split across chunks it printed raw
    if (/^<Steps>\s*$/.test(l.trim())) {
      flush();
      const inner: string[] = [];
      let j = i + 1;
      for (; j < lines.length && !/^<\/Steps>\s*$/.test(lines[j].trim()); j++) inner.push(lines[j]);
      i = j;
      const steps: { title: string; inner: Block[] }[] = [];
      const text = inner.join("\n");
      const re = /<Step\b([^>]*)>([\s\S]*?)<\/Step>/g;
      for (let m = re.exec(text); m; m = re.exec(text)) {
        const t = /\btitle\s*=\s*(?:"([^"]*)"|'([^']*)')/.exec(m[1]);
        steps.push({ title: t ? (t[1] ?? t[2] ?? "") : "", inner: parseBlocks(m[2].trim()) });
      }
      out.push({ t: "steps", steps });
      continue;
    }
    const open = /^:::(columns|callout|stats)(.*)$/.exec(l.trim());
    if (open) {
      flush();
      const inner: string[] = [];
      let j = i + 1;
      for (; j < lines.length && lines[j].trim() !== ":::"; j++) inner.push(lines[j]);
      i = j;
      if (open[1] === "columns") out.push({ t: "columns", cols: inner.join("\n").split(/\n---\n/).map((c) => parseBlocks(c)) });
      else if (open[1] === "callout") out.push({ t: "callout", inner: parseBlocks(inner.join("\n")) });
      else out.push({ t: "stats", source: /source=(\S+)/.exec(open[2])?.[1] ?? "" });
      continue;
    }
    if (LIVE_LINE.test(l)) { flush(); out.push({ t: "line", text: l }); continue; }
    buf.push(l);
  }
  flush();
  return out;
}

/** A line with live parts → its words, references, badges and actions in order (a list marker at
 *  the start is dropped; a refused action keeps only its label). */
export function splitInline(line: string): InlinePart[] {
  const parts: InlinePart[] = [];
  const text = line.replace(/^\s*[-*]\s+/, "");
  const push = (s: string) => {
    if (!s) return;
    const prev = parts[parts.length - 1];
    if (prev && prev.t === "text") prev.text += s; else parts.push({ t: "text", text: s });
  };
  const any = new RegExp(`${REF_SRC}|${BADGE_SRC}|\\[\\[action:([^\\]]*)\\]\\]`, "g");
  let last = 0;
  for (const m of text.matchAll(any)) {
    push(text.slice(last, m.index));
    if (m[1]) {
      const id = m[2].trim();
      const label = (m[3] ?? "").trim();
      parts.push({ t: "ref", kind: m[1] as RefKind, id, key: `${m[1]}:${id}`, ...(label ? { label } : {}) });
    } else if (m[4]) {
      parts.push({ t: "badge", badge: m[4] as Badge });
    } else {
      const a = parseAction(m[0]);
      if (a) parts.push({ t: "action", ...a });
      else push((m[5] ?? "").split("|").slice(1).join("|").trim());
    }
    last = (m.index ?? 0) + m[0].length;
  }
  push(text.slice(last));
  return parts;
}

/** Does this page use the rich grammar? (A page that does not renders exactly as before.) */
export function hasRichSyntax(md: string): boolean {
  const plain = withoutFences(md);
  return LIVE_LINE.test(plain) || /^:::(columns|callout|stats)/m.test(plain) || /^```view\s*$/m.test(md);
}

/** A page with one front-matter field set (added when absent; a front matter made when there is
 *  none). The value is one line: newlines and a leading "---" cannot escape the block. */
export function setFrontMatterField(md: string, key: string, value: string): string {
  const k = key.replace(/[^\w.-]/g, "");
  if (!k) return md;
  const v = value.replace(/[\r\n]+/g, " ").trim();
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(md);
  if (!m) return `---\n${k}: ${v}\n---\n${md}`;
  const lines = m[1].split("\n");
  const at = lines.findIndex((l) => l.slice(0, l.indexOf(":")).trim() === k);
  if (at >= 0) lines[at] = `${k}: ${v}`; else lines.push(`${k}: ${v}`);
  return `---\n${lines.join("\n")}\n---\n${md.slice(m[0].length)}`;
}
