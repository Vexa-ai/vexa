/** EVERY PAGE RENDERS AS A PLAIN PAGE (UI decisions 12; founder 2026-10-14: "let's apply same
 *  guidelines to the page"). Rendering only — the markdown on disk is unchanged:
 *
 *    · a "— source: …" tail becomes a small citation marker whose full text is its tooltip, and the
 *      sources are listed ONCE, deduplicated, at the bottom;
 *    · a heading with nothing under it (before the next heading of its level or above, or the end)
 *      is not drawn.
 *
 *  Pure, so every claim about what a page shows is a function a test can hold. */

/** ` — source: <text>` at the end of a line (an em dash, an en dash or a spaced hyphen). */
const SOURCE_TAIL = /\s+[—–-]\s+sources?:\s+(.+?)\s*$/i;

export type PlainPage = { body: string; sources: string[] };

/** A citation marker MDX renders as a superscript with its source as the tooltip. Quotes and braces
 *  are softened so the source can never break out of the attribute. */
export function citeMarker(n: number, source: string): string {
  const safe = source.replace(/"/g, "″").replace(/[{}<>]/g, " ").replace(/\s+/g, " ").trim();
  return `<sup className="vx-cite" title="${safe}">${n}</sup>`;
}

export function plainPage(md: string): PlainPage {
  const sources: string[] = [];
  const indexOf = (s: string) => {
    const key = s.trim();
    let i = sources.findIndex((x) => x.toLowerCase() === key.toLowerCase());
    if (i < 0) { sources.push(key); i = sources.length - 1; }
    return i + 1;
  };
  let fence = false;
  const lines = (md ?? "").split("\n").map((line) => {
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; return line; }
    if (fence || /^\s*\|/.test(line)) return line;                 // code and table rows stay as written
    const m = SOURCE_TAIL.exec(line);
    if (!m) return line;
    return line.slice(0, m.index) + " " + citeMarker(indexOf(m[1]), m[1]);
  });
  // THE PAGE'S OWN "Sources" SECTION joins the list: one list, at the bottom, each source once.
  const out: string[] = [];
  let inOwn = false;
  for (const l of lines) {
    const h = /^(#{1,6})\s+(.+?)\s*$/.exec(l);
    if (h) { inOwn = /^sources?$/i.test(h[2]); if (inOwn) continue; }
    if (inOwn) { const item = l.replace(/^\s*(?:[-*+]|\d+\.)\s+/, "").trim(); if (item) indexOf(item); continue; }
    out.push(l);
  }
  return { body: withoutEmptyHeadings(out.join("\n")), sources };
}

/** Headings that head nothing — the next non-blank line is a heading of the same or a higher level,
 *  or there is nothing after them — are dropped. A heading over a deeper heading that has content
 *  is kept. HTML comments (region markers) do not count as content. */
export function withoutEmptyHeadings(md: string): string {
  const lines = md.split("\n");
  const level = (l: string) => { const m = /^(#{1,6})\s+\S/.exec(l); return m ? m[1].length : 0; };
  const keep = lines.map(() => true);
  let fence = false;
  const inFence = lines.map((l) => { if (/^\s*(```|~~~)/.test(l)) { fence = !fence; return true; } return fence; });
  for (let i = 0; i < lines.length; i++) {
    const lv = inFence[i] ? 0 : level(lines[i]);
    if (!lv) continue;
    let j = i + 1, content = false;
    for (; j < lines.length; j++) {
      const t = lines[j].trim();
      if (!t || /^<!--.*-->$/.test(t)) continue;
      const lj = inFence[j] ? 0 : level(lines[j]);
      if (lj && lj <= lv) break;
      if (lj && lj > lv) {
        // a deeper heading counts only if it heads something itself (decided when we reach it)
        continue;
      }
      content = true;
      break;
    }
    if (!content) keep[i] = false;
  }
  return lines.filter((_, i) => keep[i]).join("\n");
}

/** The bottom list: "Sources" and each source once, numbered as the markers are. */
export function sourcesBlock(sources: string[]): string {
  if (!sources.length) return "";
  return `\n\n---\n\n<div className="vx-sources">\n\n**Sources**\n\n${sources.map((s, i) => `${i + 1}. ${s}`).join("\n")}\n\n</div>\n`;
}
