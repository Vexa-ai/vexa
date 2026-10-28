/** Is this file a meeting bundle (meeting-bundle.v1)? Decided from the file's own bytes, by the
 *  contract's first rules, never from its name.
 *
 *  `deploy/contracts/meeting-bundle.v1/README.md` § Importing a bundle safely, rules 1 and 2: a bundle
 *  is a zip with a `manifest.json`, and that manifest names the `meeting-bundle` contract. This reads
 *  the zip's central directory the way the contract's reference reader (`validate.mjs`) does — the
 *  end-of-central-directory record, then each entry's name — and, when the manifest can be inflated
 *  here, its `contract` field. Nothing more: the hashes, the schema of every part and each media
 *  signature are the importer's to check (meeting-api `read_bundle`), and a file that claims to be a
 *  bundle and fails those checks is REFUSED by that importer with its code, not quietly attached.
 *
 *  So the answer is a claim, not a verdict:
 *    · `bundle`     — a zip whose root holds `manifest.json` and `meeting.json`, and whose manifest
 *                     (when readable) names a `meeting-bundle.*` contract. It goes to the import route.
 *    · `not_bundle` — anything else (not a zip, a zip without those entries, a manifest of another
 *                     contract). It is an ordinary attachment.
 *
 *  Only the tail of the file (the directory) and the manifest entry are read — a 500 MB recording
 *  inside a bundle is never loaded to decide this. */

const EOCD_SIG = 0x06054b50;
const CDH_SIG = 0x02014b50;
const LFH_SIG = 0x04034b50;
/** The EOCD is the last 22 bytes plus a comment of at most 65,535 (APPNOTE 4.3.16). */
const EOCD_SEARCH = 22 + 0xffff;
/** A manifest is small: the contract caps a JSON part at 32 MiB, but a manifest of 2,001 entries is
 *  well under 1 MiB. Anything bigger is not read here (the importer still checks it). */
const MANIFEST_READ_CAP = 4 * 1024 * 1024;
/** The central directory of a bundle of 2,001 entries with long workspace paths stays far below this. */
const DIRECTORY_READ_CAP = 8 * 1024 * 1024;

export type BundleClaim = "bundle" | "not_bundle";

interface Entry { name: string; method: number; csize: number; offset: number }

async function bytes(file: Blob, start: number, end: number): Promise<DataView> {
  const part = file.slice(Math.max(0, start), Math.max(0, end));
  return new DataView(await readBlob(part));
}

/** Blob → ArrayBuffer, also where `Blob.arrayBuffer` is missing (older jsdom). */
function readBlob(b: Blob): Promise<ArrayBuffer> {
  if (typeof b.arrayBuffer === "function") return b.arrayBuffer();
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as ArrayBuffer);
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(b);
  });
}

const utf8 = new TextDecoder("utf-8");

/** The central directory's entries, or null when this is not a readable zip. */
async function directory(file: Blob): Promise<Entry[] | null> {
  if (file.size < 22) return null;
  const head = await bytes(file, 0, 4);
  // An empty zip starts with its EOCD; a bundle never is one, so a local header must lead.
  if (head.getUint32(0, true) !== LFH_SIG) return null;
  const tailStart = Math.max(0, file.size - EOCD_SEARCH);
  const tail = await bytes(file, tailStart, file.size);
  let eocd = -1;
  for (let i = tail.byteLength - 22; i >= 0; i--) {
    if (tail.getUint32(i, true) === EOCD_SIG) { eocd = i; break; }
  }
  if (eocd < 0) return null;
  const count = tail.getUint16(eocd + 10, true);
  const size = tail.getUint32(eocd + 12, true);
  const offset = tail.getUint32(eocd + 16, true);
  if (size > DIRECTORY_READ_CAP || offset + size > file.size) return null;
  const cd = await bytes(file, offset, offset + size);
  const out: Entry[] = [];
  let p = 0;
  for (let n = 0; n < count; n++) {
    if (p + 46 > cd.byteLength || cd.getUint32(p, true) !== CDH_SIG) return null;
    const nameLen = cd.getUint16(p + 28, true), extraLen = cd.getUint16(p + 30, true), commentLen = cd.getUint16(p + 32, true);
    if (p + 46 + nameLen > cd.byteLength) return null;
    out.push({
      name: utf8.decode(new Uint8Array(cd.buffer, cd.byteOffset + p + 46, nameLen)),
      method: cd.getUint16(p + 10, true),
      csize: cd.getUint32(p + 20, true),
      offset: cd.getUint32(p + 42, true),
    });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

/** The manifest's `contract`, or undefined when it cannot be read HERE (an inflater this browser
 *  lacks, a method other than stored/deflate, a manifest that is not JSON). Undefined is not a
 *  refusal: the importer reads it again and answers with its own code. */
async function manifestContract(file: Blob, e: Entry): Promise<string | undefined> {
  if (e.csize > MANIFEST_READ_CAP) return undefined;
  const lfh = await bytes(file, e.offset, e.offset + 30);
  if (lfh.byteLength < 30 || lfh.getUint32(0, true) !== LFH_SIG) return undefined;
  const start = e.offset + 30 + lfh.getUint16(26, true) + lfh.getUint16(28, true);
  const raw = file.slice(start, start + e.csize);
  let text: string;
  try {
    if (e.method === 0) text = utf8.decode(await readBlob(raw));
    else if (e.method === 8 && typeof DecompressionStream !== "undefined") {
      const stream = new Response(new Uint8Array(await readBlob(raw))).body!.pipeThrough(new DecompressionStream("deflate-raw"));
      text = await new Response(stream).text();
    } else return undefined;
    const doc = JSON.parse(text) as { contract?: unknown };
    return typeof doc?.contract === "string" ? doc.contract : "";
  } catch {
    return undefined;
  }
}

/** Does `file` claim to be a meeting bundle? See the module comment for what is and is not checked. */
export async function sniffMeetingBundle(file: Blob): Promise<BundleClaim> {
  let entries: Entry[] | null;
  try { entries = await directory(file); } catch { return "not_bundle"; }
  if (!entries) return "not_bundle";
  const manifest = entries.find((e) => e.name === "manifest.json");
  if (!manifest || !entries.some((e) => e.name === "meeting.json")) return "not_bundle";
  const contract = await manifestContract(file, manifest).catch(() => undefined);
  // A readable manifest of ANOTHER contract is someone else's zip. One naming any meeting-bundle
  // version is ours to answer — an unknown major is refused by the importer as unsupported_version,
  // which the person needs to hear, not a silent attachment.
  if (contract !== undefined && !contract.startsWith("meeting-bundle")) return "not_bundle";
  return "bundle";
}
