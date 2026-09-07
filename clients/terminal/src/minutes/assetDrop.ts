/** assetDrop — dropping or pasting a picture into a page (Vexa-ai/vexa#1612).
 *
 *  The founder's rule for images is one directory and one shape of reference: whoever puts a
 *  picture on a page — the agent fetching a logo, a person dragging a chart out of Slack — the bytes
 *  land in the workspace under `assets/` and the page carries a RELATIVE reference to them. A paste
 *  that inlined a `data:` URI, or a drop that left the file on the desktop and wrote its `file://`
 *  path, would each produce a page that renders once, for one person, on one machine.
 *
 *  The mechanics live here rather than in `MarkdownEditor` because a CodeMirror event handler is
 *  the one place in this client that cannot be tested without a real browser — so the editor keeps
 *  the three lines that need a live view (where the cursor is, what text to put there) and
 *  everything that decides WHAT happens is a plain function with a test.
 */

/** Everything a page can show inline. A drop of anything else is not refused — it is stored and
 *  linked as a file, because "I dropped a PDF on the page" is a reasonable thing to have meant. */
const IMAGE = /^image\//i;

/** The files carried by a drop or a paste, in the order the person gave them.
 *
 *  A paste carries `items` and a drop carries `files`; a paste of a picture out of a screenshot
 *  tool arrives as an `image/png` item with an EMPTY name, which is why the caller names it. */
export function filesFromTransfer(dt: DataTransfer | null | undefined): File[] {
  if (!dt) return [];
  const out: File[] = [];
  if (dt.files && dt.files.length) out.push(...Array.from(dt.files));
  else if (dt.items) {
    for (const item of Array.from(dt.items)) {
      if (item.kind !== "file") continue;
      const f = item.getAsFile();
      if (f) out.push(f);
    }
  }
  return out;
}

/** The name a pasted screenshot is stored under. A paste has no filename, and `image.png` for every
 *  one of them would make the second paste overwrite the first — so the clock names it. */
export function assetName(file: File, now: Date = new Date()): string {
  if (file.name) return file.name;
  const ext = (file.type.split("/")[1] || "png").replace(/[^a-z0-9]/gi, "") || "png";
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
  return `pasted-${stamp}.${ext}`;
}

/** The markdown a stored file becomes: an image if a page can show it, else a link. */
export function referenceFor(path: string, contentType: string, label: string): string {
  const name = label || path.split("/").pop() || path;
  return IMAGE.test(contentType) ? `![${name}](${path})` : `[${name}](${path})`;
}

/** Put `text` in at the selection, and say where the cursor goes after it — the pure half of
 *  "insert this reference where they dropped it". Blank lines around a block-level image so it does
 *  not glue itself to the paragraph it landed on. */
export function insertAt(value: string, from: number, to: number, text: string): { value: string; cursor: number } {
  const before = value.slice(0, from);
  const after = value.slice(to);
  const lead = before === "" || before.endsWith("\n") ? "" : "\n";
  const trail = after.startsWith("\n") || after === "" ? "" : "\n";
  const insert = `${lead}${text}${trail}`;
  return { value: before + insert + after, cursor: from + insert.length };
}

export interface DroppedAsset { path: string; reference: string }

/** Store every file from a drop or paste and hand back the markdown for it, in order.
 *
 *  `upload` is injected so the caller's workspace (and this module's test) decide where the bytes
 *  go; nothing here knows about HTTP. A file that fails to store is REPORTED, never silently
 *  dropped: a picture that vanishes between the drop and the save is the failure this whole change
 *  exists to stop happening. */
export async function storeDropped(
  files: File[],
  upload: (file: File, name: string) => Promise<{ path: string; content_type: string }>,
  now: Date = new Date(),
): Promise<{ assets: DroppedAsset[]; failed: string[] }> {
  const assets: DroppedAsset[] = [];
  const failed: string[] = [];
  for (const file of files) {
    const name = assetName(file, now);
    try {
      const stored = await upload(file, name);
      assets.push({ path: stored.path, reference: referenceFor(stored.path, stored.content_type || file.type, name) });
    } catch {
      failed.push(name);
    }
  }
  return { assets, failed };
}
