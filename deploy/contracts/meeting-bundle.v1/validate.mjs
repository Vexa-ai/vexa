#!/usr/bin/env node
/**
 * gate:schema for meeting-bundle.v1 — the contract re-derived in a second language, from the bytes.
 *
 *   1. Every JSON golden `golden/<Shape>.<case>.json` conforms to `#/$defs/<Shape>`.
 *   2. Every bundle in `golden/bundles/*.zip` is ACCEPTED: the zip is read here with no library
 *      (central directory + node:zlib), every entry passes the path/entry rules, the manifest
 *      conforms, every listed file is present with its exact length and SHA-256, and each part
 *      conforms to its shape.
 *   3. Every bundle in `golden/refused/*.zip` is REFUSED with exactly the code
 *      `golden/refused/refused.json` names for it — the same code the Python importer gives it
 *      (meeting-api `tests/test_meeting_bundle_contract.py` reads the same files).
 *
 * This file is also a reference importer front gate for third parties: `node validate.mjs --file
 * my.zip` prints ACCEPT or the refusal code for any bundle.
 * Run: node validate.mjs [--check] [--file PATH]...
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { createHash } from "node:crypto";
import { inflateRawSync } from "node:zlib";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(HERE, "meeting-bundle.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: false });
addFormats(ajv);
ajv.addSchema(schema);
const shape = (name) => ajv.compile({ $ref: `${schema.$id}#/$defs/${name}` });

// The caps and grammar every importer applies (README § Limits). Kept equal to the Python codec's.
const MAX_BUNDLE_BYTES = 512 * 1024 * 1024;
const MAX_ENTRIES = 2001;
const MAX_TOTAL = 1024 * 1024 * 1024;
const MAX_JSON = 32 * 1024 * 1024;
const MAX_WORKSPACE_FILE = 16 * 1024 * 1024;
const MAX_RATIO = 200;
const MAX_NOTES = 1024 * 1024;
const MAX_PARTS = 256 * 1024 * 1024;
const RATIO_FLOOR = 1024 * 1024;
const ENTRY = new RegExp(schema.$defs.EntryPath.pattern);

class Refused extends Error { constructor(code, detail) { super(`${code}: ${detail}`); this.code = code; } }
const refuse = (code, detail) => { throw new Refused(code, detail); };

/** The central directory, read without a library. Returns [{name, flags, method, csize, usize, mode, offset}]. */
function centralDirectory(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) refuse("not_a_bundle", "not a zip archive (no end-of-central-directory record)");
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const out = [];
  for (let n = 0; n < count; n++) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== 0x02014b50) refuse("not_a_bundle", "corrupt central directory");
    const nameLen = buf.readUInt16LE(p + 28), extraLen = buf.readUInt16LE(p + 30), commentLen = buf.readUInt16LE(p + 32);
    out.push({
      name: buf.toString("utf8", p + 46, p + 46 + nameLen),
      flags: buf.readUInt16LE(p + 8),
      method: buf.readUInt16LE(p + 10),
      csize: buf.readUInt32LE(p + 20),
      usize: buf.readUInt32LE(p + 24),
      mode: buf.readUInt32LE(p + 38) >>> 16,
      offset: buf.readUInt32LE(p + 42),
    });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

function entryProblem(e) {
  const n = e.name;
  if (n.endsWith("/")) return ["unsafe_entry", `${n}: directory entries are not part of a bundle`];
  if (n.startsWith("/") || n.includes("\\") || /^[A-Za-z]:/.test(n) || n.split("/").some((s) => s === "" || s === "." || s === ".."))
    return ["unsafe_path", `${n}: a bundle path is relative, forward-slash, with no '.' or '..'`];
  const fmt = e.mode & 0o170000;
  if (e.mode && fmt !== 0 && fmt !== 0o100000) return ["unsafe_entry", `${n} is ${fmt === 0o120000 ? "a symlink" : "not a regular file"}`];
  if (e.flags & 1) return ["unsafe_entry", `${n} is encrypted`];
  if (e.method !== 0 && e.method !== 8) return ["unsafe_entry", `${n}: compression method ${e.method}`];
  if (n !== "manifest.json" && !ENTRY.test(n)) return ["unsafe_path", `${n} is not a path a meeting bundle may hold`];
  return null;
}

const capFor = (n) => (n === "notes.md" ? MAX_NOTES : n.endsWith(".json") && !n.includes("/") ? MAX_JSON : n.startsWith("workspace/") ? MAX_WORKSPACE_FILE : MAX_TOTAL);

/** notes.md is UTF-8 text with no NUL — a page, never a binary in disguise. */
function checkNotes(bytes) {
  if (bytes.length > MAX_NOTES) refuse("too_large", "notes.md");
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { refuse("invalid_part", "notes.md is not UTF-8 text"); }
  if (text.includes("\u0000")) refuse("invalid_part", "notes.md contains a NUL byte");
}

function readEntry(buf, e, cap) {
  const p = e.offset;
  if (p + 30 > buf.length || buf.readUInt32LE(p) !== 0x04034b50) refuse("not_a_bundle", `${e.name}: corrupt local header`);
  const start = p + 30 + buf.readUInt16LE(p + 26) + buf.readUInt16LE(p + 28);
  const raw = buf.subarray(start, start + e.csize);
  if (e.method === 0) return Buffer.from(raw);
  try { return inflateRawSync(raw, { maxOutputLength: cap }); }
  catch (err) {
    if (err.code === "ERR_BUFFER_TOO_LARGE" || /larger than|maxOutputLength/i.test(String(err.message))) refuse("too_large", `${e.name} inflates past ${cap}`);
    refuse("not_a_bundle", `${e.name} cannot be inflated: ${err.message}`);
  }
}

const json = (name, bytes) => { try { return JSON.parse(bytes.toString("utf8")); } catch (e) { refuse("invalid_part", `${name} is not JSON`); } };
const conformOr = (sh, name, doc) => { const v = shape(sh); if (!v(doc)) refuse("invalid_part", `${name}: ${ajv.errorsText(v.errors)}`); };
const MAGIC = {
  webm: (b) => b.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])),
  mkv: (b) => b.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])),
  wav: (b) => b.toString("latin1", 0, 4) === "RIFF" && b.toString("latin1", 8, 12) === "WAVE",
  mp4: (b) => b.toString("latin1", 4, 8) === "ftyp",
};

function checkedEntries(buf) {
  const entries = centralDirectory(buf);
  if (entries.length > MAX_ENTRIES) refuse("too_large", `${entries.length} entries`);
  const seen = new Map();
  let total = 0;
  for (const e of entries) {
    const problem = entryProblem(e);
    if (problem) refuse(...problem);
    if (seen.has(e.name)) refuse("unsafe_entry", `${e.name} appears twice`);
    seen.set(e.name, e);
    const cap = capFor(e.name);
    if (e.usize > cap) refuse("too_large", `${e.name} declares ${e.usize} bytes`);
    total += e.usize;
    if (e.method === 8 && e.usize > RATIO_FLOOR && e.usize > MAX_RATIO * Math.max(e.csize, 1)) refuse("too_large", `${e.name} inflates more than ${MAX_RATIO}:1`);
  }
  if (total > MAX_TOTAL) refuse("too_large", `inflates to ${total} bytes`);
  return seen;
}

/** The parts archive an export takes: only workspace/** and notes.md, under the bundle's entry rules. */
export function readParts(buf) {
  if (buf.length > MAX_PARTS) refuse("too_large", `${buf.length} bytes`);
  const seen = checkedEntries(buf);
  let files = 0, notes = false;
  for (const [n, e] of seen) {
    if (n === "notes.md") { checkNotes(readEntry(buf, e, MAX_NOTES)); notes = true; }
    else if (n.startsWith("workspace/")) { readEntry(buf, e, MAX_WORKSPACE_FILE); files++; }
    else refuse("manifest_mismatch", `${n} is not a part an export takes`);
  }
  return { workspace_files: files, notes_page: notes };
}

/** Accept (returns a summary) or throw Refused — the same rules, in the same order, as the Python codec. */
export function readBundle(buf) {
  if (buf.length > MAX_BUNDLE_BYTES) refuse("too_large", `${buf.length} bytes`);
  const seen = checkedEntries(buf);
  if (!seen.has("manifest.json")) refuse("not_a_bundle", "no manifest.json");
  const manifest = json("manifest.json", readEntry(buf, seen.get("manifest.json"), MAX_JSON));
  if (manifest?.contract !== "meeting-bundle.v1") refuse("unsupported_version", `contract ${manifest?.contract}`);
  const major = String(manifest.format_version ?? "").split(".")[0];
  if (!/^\d+$/.test(major) || Number(major) !== 1) refuse("unsupported_version", `format_version ${manifest.format_version}`);
  conformOr("Manifest", "manifest.json", manifest);
  const listed = new Map();
  for (const f of manifest.files) { if (listed.has(f.path)) refuse("manifest_mismatch", `${f.path} listed twice`); listed.set(f.path, f); }
  const present = [...seen.keys()].filter((n) => n !== "manifest.json");
  const unlisted = present.filter((n) => !listed.has(n));
  if (unlisted.length) refuse("manifest_mismatch", `unlisted: ${unlisted.slice(0, 5)}`);
  const missing = [...listed.keys()].filter((n) => !seen.has(n));
  if (missing.length) refuse("manifest_mismatch", `missing: ${missing.slice(0, 5)}`);
  const roleOf = { "meeting.json": "meeting", "transcript.json": "transcript", "annotations.json": "annotations", "notes.md": "notes" };
  for (const [p, f] of listed) { const want = roleOf[p] ?? p.split("/")[0]; if (f.role !== want) refuse("manifest_mismatch", `${p} role ${f.role}`); }
  for (const req of ["meeting.json", "transcript.json"]) if (!listed.has(req)) refuse("manifest_mismatch", `missing ${req}`);
  const blobs = new Map();
  for (const [p, f] of listed) {
    const data = readEntry(buf, seen.get(p), capFor(p));
    if (data.length !== f.bytes || createHash("sha256").update(data).digest("hex") !== f.sha256) refuse("hash_mismatch", `${p}`);
    blobs.set(p, data);
  }
  if (blobs.has("notes.md")) checkNotes(blobs.get("notes.md"));
  const meeting = json("meeting.json", blobs.get("meeting.json"));
  const transcript = json("transcript.json", blobs.get("transcript.json"));
  const annotations = blobs.has("annotations.json") ? json("annotations.json", blobs.get("annotations.json")) : { metadata: {}, notes: null };
  conformOr("Meeting", "meeting.json", meeting);
  conformOr("Transcript", "transcript.json", transcript);
  conformOr("Annotations", "annotations.json", annotations);
  if (Buffer.byteLength(JSON.stringify(annotations.metadata)) > 16 * 1024) refuse("invalid_part", "metadata exceeds 16 KiB");
  const declared = new Map(meeting.media.map((m) => [m.path, m]));
  for (const p of [...listed.keys()].filter((x) => x.startsWith("media/")).sort()) {
    const part = declared.get(p);
    if (!part) refuse("manifest_mismatch", `${p} not declared in meeting.json`);
    if (!p.endsWith("." + part.format)) refuse("unsafe_media", `${p} format`);
    if (!MAGIC[part.format](blobs.get(p))) refuse("unsafe_media", `${p} is not a ${part.format} container`);
  }
  const undelivered = [...declared.keys()].filter((p) => !listed.has(p));
  if (undelivered.length) refuse("manifest_mismatch", `undelivered media ${undelivered}`);
  return { bundle_id: manifest.bundle_id, segments: transcript.segments.length, media: meeting.media.length, files: listed.size };
}

function verdict(path, reader = readBundle) {
  try { return { ok: true, summary: reader(readFileSync(path)) }; }
  catch (e) { if (e instanceof Refused) return { ok: false, code: e.code, detail: e.message }; throw e; }
}

const extra = [];
for (let i = 2; i < process.argv.length; i++) if (process.argv[i] === "--file") extra.push(process.argv[++i]);
if (extra.length) {
  let bad = 0;
  for (const f of extra) { const v = verdict(f); console.log(v.ok ? `ACCEPT ${f} ${JSON.stringify(v.summary)}` : `REFUSE ${f} ${v.code} — ${v.detail}`); if (!v.ok) bad++; }
  process.exit(bad ? 1 : 0);
}

let failed = 0, checked = 0;
const G = join(HERE, "golden");
for (const f of readdirSync(G).filter((n) => n.endsWith(".json"))) {
  checked++;
  const sh = f.split(".")[0];
  const v = shape(sh);
  if (!v(JSON.parse(readFileSync(join(G, f), "utf8")))) { console.error(`  ✗ ${f} (${sh}): ${ajv.errorsText(v.errors)}`); failed++; }
  else console.log(`  ✓ ${f} ≡ ${sh}`);
}
const B = join(G, "bundles");
for (const f of existsSync(B) ? readdirSync(B).filter((n) => n.endsWith(".zip")) : []) {
  checked++;
  const v = verdict(join(B, f));
  if (!v.ok) { console.error(`  ✗ bundles/${f} must be accepted, refused: ${v.code} ${v.detail}`); failed++; }
  else console.log(`  ✓ bundles/${f} accepted (${v.summary.segments} segment(s), ${v.summary.media} media)`);
}
const R = join(G, "refused");
const vectors = existsSync(join(R, "refused.json")) ? JSON.parse(readFileSync(join(R, "refused.json"), "utf8")) : [];
const vv = shape("RefusedVector");
const zips = new Set(existsSync(R) ? readdirSync(R).filter((n) => n.endsWith(".zip")) : []);
for (const vec of vectors) {
  checked++;
  if (!vv(vec)) { console.error(`  ✗ refused.json row: ${ajv.errorsText(vv.errors)}`); failed++; continue; }
  zips.delete(vec.bundle);
  const v = verdict(join(R, vec.bundle));
  if (v.ok || v.code !== vec.code) { console.error(`  ✗ refused/${vec.bundle}: expected ${vec.code}, got ${v.ok ? "ACCEPT" : v.code}`); failed++; }
  else console.log(`  ✓ refused/${vec.bundle} → ${vec.code}`);
}
for (const z of zips) { console.error(`  ✗ refused/${z} has no row in refused.json`); failed++; }
// The parts archive: every zip in golden/parts/ is accepted unless refused.json names its code.
const P = join(G, "parts");
const partRows = existsSync(join(P, "refused.json")) ? JSON.parse(readFileSync(join(P, "refused.json"), "utf8")) : [];
const partCodes = new Map(partRows.map((r) => [r.bundle, r.code]));
for (const r of partRows) if (!vv(r)) { console.error(`  ✗ parts/refused.json row: ${ajv.errorsText(vv.errors)}`); failed++; }
for (const f of existsSync(P) ? readdirSync(P).filter((n) => n.endsWith(".zip")).sort() : []) {
  checked++;
  const v = verdict(join(P, f), readParts);
  const want = partCodes.get(f);
  if (want ? (v.ok || v.code !== want) : !v.ok) { console.error(`  ✗ parts/${f}: expected ${want ?? "ACCEPT"}, got ${v.ok ? "ACCEPT" : v.code}`); failed++; }
  else console.log(`  ✓ parts/${f} → ${want ?? "accepted"}`);
}
console.log(failed ? `meeting-bundle.v1: ${failed} FAILED` : `meeting-bundle.v1: ${checked} golden(s) conform`);
process.exit(failed ? 1 : 0);
