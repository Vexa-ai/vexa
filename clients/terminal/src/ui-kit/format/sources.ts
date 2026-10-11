/** An agent's sources, as data (guidelines §4.17, §3.11 of the audit).
 *
 *  Two ways in:
 *    · STRUCTURED — a `sources` event on the chat stream carrying `{ title, url, date?, snippet? }`
 *      items (`readSources`). Additive: a server that does not send it changes nothing.
 *    · FALLBACK — the agent wrote its sources as a Markdown list under a "Sources" label, e.g.
 *        **Sources**
 *        - [Example announces platform 2026-10-08](https://news.example.com/a) (2026-10-08)
 *      `extractSources` lifts that block out of the prose so it renders as a SourceList, with the
 *      date shown ONCE: a date already in the title is not repeated beside it (the founder's
 *      screenshot showed "2026-10-08 … (2026-10-08)").
 *  Only http(s) URLs are accepted; anything else is not a source and the block stays prose. */
import type { Source } from "../primitives/SourceList";

const LABEL = /^\s*(?:#{1,6}\s*|\*\*|__)?\s*(sources|references|citations|links)\s*:?\s*(?:\*\*|__)?\s*:?\s*$/i;
const ITEM = /^\s*(?:[-*]|\d+[.)])\s+(.*)$/;
const LINK = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/;
const BARE = /(https?:\/\/[^\s)]+)/;
const DATE = /\b(\d{4}-\d{2}-\d{2})\b/;

/** One list item → a Source, or null when it is not a link. */
export function parseSourceItem(item: string): Source | null {
  const m = LINK.exec(item);
  let title: string, url: string, rest: string;
  if (m) { title = m[1].trim(); url = m[2]; rest = (item.slice(0, m.index) + " " + item.slice(m.index + m[0].length)).trim(); }
  else {
    const b = BARE.exec(item);
    if (!b) return null;
    url = b[1];
    rest = (item.slice(0, b.index) + item.slice(b.index + b[0].length)).trim();
    title = rest.replace(/^[\s:–—-]+|[\s:–—-]+$/g, "") || url.replace(/^https?:\/\//, "");
    rest = "";
  }
  const date = DATE.exec(title)?.[1] ?? DATE.exec(rest)?.[1];
  // what is left beside the link, minus dates and separators, is a snippet
  const snippet = rest.replace(/\(?\b\d{4}-\d{2}-\d{2}\b\)?/g, "").replace(/^[\s:–—·,-]+|[\s:–—·,-]+$/g, "").trim();
  return { title, url, ...(date ? { date } : {}), ...(snippet ? { snippet } : {}) };
}

/** Lift a "Sources" list out of a Markdown body. Returns the body without it, and the sources;
 *  `sources` is empty (and `body` unchanged) when there is no such block or any item is not a link. */
export function extractSources(markdown: string): { body: string; sources: Source[] } {
  const lines = markdown.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (!LABEL.test(lines[i])) continue;
    let j = i + 1;
    while (j < lines.length && lines[j].trim() === "") j++;
    const items: Source[] = [];
    let k = j;
    for (; k < lines.length; k++) {
      const it = ITEM.exec(lines[k]);
      if (!it) break;
      const s = parseSourceItem(it[1]);
      if (!s) return { body: markdown, sources: [] };
      items.push(s);
    }
    if (items.length === 0) continue;
    const body = [...lines.slice(0, i), ...lines.slice(k)].join("\n").replace(/\n{3,}/g, "\n\n").trimEnd();
    return { body, sources: items };
  }
  return { body: markdown, sources: [] };
}

/** Validate a structured `sources` payload from the stream. Unknown shapes are dropped. */
export function readSources(v: unknown): Source[] {
  if (!Array.isArray(v)) return [];
  const out: Source[] = [];
  for (const x of v) {
    if (!x || typeof x !== "object") continue;
    const o = x as Record<string, unknown>;
    if (typeof o.url !== "string" || !/^https?:\/\//i.test(o.url)) continue;
    const title = typeof o.title === "string" && o.title.trim() ? o.title.trim() : o.url.replace(/^https?:\/\//, "");
    out.push({ title, url: o.url,
      ...(typeof o.date === "string" ? { date: o.date } : {}),
      ...(typeof o.snippet === "string" && o.snippet.trim() ? { snippet: o.snippet.trim() } : {}) });
  }
  return out.slice(0, 50);
}

/** A turn's sources, merged by URL: a later item with a title fills in an earlier one's; order is
 *  first-seen. agent-api sends one fetched page per `sources` frame. */
export function mergeSources(prev: Source[], next: Source[]): Source[] {
  const out = prev.map((s) => ({ ...s }));
  for (const n of next) {
    const i = out.findIndex((s) => s.url === n.url);
    if (i < 0) out.push(n);
    else out[i] = { ...out[i], ...Object.fromEntries(Object.entries(n).filter(([, v]) => v)) } as Source;
  }
  return out.slice(0, 50);
}
