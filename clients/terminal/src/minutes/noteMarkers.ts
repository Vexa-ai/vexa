/** A MEETING NOTE'S MACHINERY, KEPT OUT OF THE PERSON'S SIGHT WHILE THEY EDIT IT (#1783).
 *
 *  A meeting's note carries two kinds of HTML comment the product writes and reads: the transcript
 *  marker (`<!-- vexa:transcript meeting=119 -->`) and the region fences around each section an update
 *  rewrites (`<!-- meeting:decisions:start -->` … `<!-- meeting:decisions:end -->`). The founder,
 *  2026-10-15, opened the note for editing and met them as raw text: they are never shown to a
 *  person. `hideNoteMarkers` takes them out of the text an editor shows and remembers where they were;
 *  `restoreNoteMarkers` puts them back around the same sections when the text is saved, so the next
 *  Update notes still writes into the regions instead of appending a second copy of each section.
 *
 *  A region is anchored to the heading directly above its start fence (the shape `write_region`
 *  writes). A section the person deleted loses its fences with it — the next update adds the section
 *  again, which is what an update does for a section that is not there. Pure; no I/O. */

const SLOT = /^\s*<!--\s*vexa:transcript\s+meeting=["']?[A-Za-z0-9_.:-]{1,128}["']?\s*-->\s*$/;
const FENCE = /^\s*<!--\s*meeting:([a-z]+):(start|end)\s*-->\s*$/;
const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;

export interface NoteMarkers {
  /** the transcript marker line, when the note had one */
  slot: string;
  /** the line the marker stood under ("" = the top of the note) */
  slotAfter?: string;
  /** region key → the heading line its start fence sat under, and the first line of the person's
   *  own text after its end fence in the same section (where the region stopped), if any */
  regions: { key: string; heading: string; after?: string }[];
}

/** The note body (frontmatter already split off) with every product marker line removed, and what
 *  was removed. Fenced code is left alone: an example of a marker inside ``` stays an example. */
export function hideNoteMarkers(body: string): { text: string; markers: NoteMarkers } {
  const lines = String(body ?? "").split("\n");
  const out: string[] = [];
  const markers: NoteMarkers = { slot: "", regions: [] };
  let fenced = false;
  let lastHeading = "";
  let closed: { key: string; heading: string; after?: string } | null = null;   // waiting for its `after`
  let skipBlank = false;
  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (skipBlank) { skipBlank = false; if (!line.trim()) continue; }
    if (!fenced) {
      if (SLOT.test(line)) {
        if (!markers.slot) {
          markers.slot = line.trim();
          markers.slotAfter = [...out].reverse().find((l) => l.trim())?.trim() ?? "";
        }
        skipBlank = true;     // the blank line that set it apart goes with it, and comes back with it
        continue;
      }
      const f = FENCE.exec(line);
      if (f) {
        if (f[2] === "start" && lastHeading && !markers.regions.some((r) => r.key === f[1])) {
          markers.regions.push({ key: f[1], heading: lastHeading });
        }
        if (f[2] === "end") closed = markers.regions.find((r) => r.key === f[1]) ?? null;
        continue;
      }
      if (HEADING.test(line)) { lastHeading = line.trim(); closed = null; }
      else if (closed && line.trim()) { closed.after = line.trim(); closed = null; }
    }
    out.push(line);
  }
  return { text: out.join("\n"), markers };
}

/** The edited text with the markers put back: the transcript marker on top, each region's fences
 *  around the content of the section under the same heading. */
export function restoreNoteMarkers(text: string, markers: NoteMarkers): string {
  const lines = String(text ?? "").split("\n");
  const level = (l: string) => HEADING.exec(l)?.[1].length ?? 0;
  for (const { key, heading, after } of markers.regions) {
    const i = lines.findIndex((l) => l.trim() === heading);
    if (i < 0) continue;
    const lv = level(lines[i]);
    let j = i + 1;
    while (j < lines.length && !(level(lines[j]) && level(lines[j]) <= lv)) j++;
    // the person's own text after the region stays outside it
    if (after) { const k = lines.slice(i + 1, j).findIndex((l) => l.trim() === after); if (k >= 0) j = i + 1 + k; }
    // the section's content without the blank lines that separate it from the next heading
    let end = j;
    while (end > i + 1 && !lines[end - 1].trim()) end--;
    let start = i + 1;
    while (start < end && !lines[start].trim()) start++;
    const inner = lines.slice(start, end);
    const tail = lines.slice(end, j);
    lines.splice(i + 1, j - (i + 1),
      `<!-- meeting:${key}:start -->`, ...inner, `<!-- meeting:${key}:end -->`, ...tail);
  }
  if (!markers.slot) return lines.join("\n");
  const at = markers.slotAfter ? lines.findIndex((l) => l.trim() === markers.slotAfter) : -1;
  if (at < 0) return `${markers.slot}\n\n${lines.join("\n")}`;
  lines.splice(at + 1, 0, "", markers.slot);
  return lines.join("\n");
}

/** A meeting note's WHOLE FILE as an editor should show it: the frontmatter and every product marker
 *  held back, the words only — and `restore` to put the edited words back into a whole file. A file
 *  that is not a meeting note (no transcript marker, no region fence) is edited as it is. */
export function editableNote(raw: string): { text: string; restore: (text: string) => string } {
  const src = String(raw ?? "");
  const m = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/.exec(src);
  const fm = m ? m[0] : "";
  const rest = src.slice(fm.length);
  const lead = /^\n*/.exec(rest)?.[0] ?? "";
  const { text, markers } = hideNoteMarkers(rest.slice(lead.length));
  if (!markers.slot && !markers.regions.length) return { text: src, restore: (t) => t };
  return { text, restore: (t) => fm + lead + restoreNoteMarkers(t, markers) };
}
