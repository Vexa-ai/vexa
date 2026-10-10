#!/usr/bin/env node
/**
 * The VENDOR-PAYLOAD ABSENCE checker: the check ADR-0039 promised for the optional, operator-supplied
 * native meeting runtime, and the mechanism P17 names for that category.
 *
 * P17 allows an optional operator-supplied runtime that is not a dependency, and only while Vexa never
 * commits, vendors, downloads or bakes it; it is off by default and absent from every default
 * entrypoint and from compose, Helm, Lite and stock dispatch; it is reached only from a disposable
 * subprocess behind sealed contracts; and it is logged in a manifest row. Each clause is a check here:
 *
 *   tracked       no tracked file is a native payload: a shared object (`.so`, `.so.N`, `.dylib`, `.dll`),
 *                 a static library (`.a`), a compiled Node addon (`.node`), the SDK library
 *                 (`libmeetingsdk*`), its header (`zoom_sdk.h`) or its bundled Qt tree (`qt_libs`), anywhere;
 *                 and any archive under the native runtime's tree (`native-meeting/`). Repository-wide,
 *                 from `git ls-files`.
 *   excluded      the `native-sdk-exclusion` block is present in `.gitignore`, the root `.dockerignore`
 *                 and `deploy/lite/Dockerfile.lite.dockerignore`, and the three are one fact:
 *                 `scripts/parity.json` carries it as an ENFORCED fact over exactly those files, and it
 *                 holds. The block DENIES BY DEFAULT under `native-meeting/native/` (a list of names
 *                 admits an SDK unpacked under any other name; security pass 3, D-11) and re-includes only
 *                 tracked, non-payload files. Git must ignore payload-shaped and unknown names there.
 *   fetched       no package manifest, image recipe, workflow, Makefile or shell script names the payload or
 *                 builds the wrapper, so nothing Vexa runs downloads or compiles it.
 *   reached       only the declared subprocess (the `operatorSupplied` row's `subprocess`) loads a native
 *                 addon: no other tracked code `require`s a `.node` file, the wrapper, or the addon path
 *                 from `ZOOM_SDK_ADDON`, and none calls `process.dlopen`.
 *   unreferenced  nothing a stock install builds or runs names the native path: the bot's `src/`, its
 *                 package manifest, image recipe and entrypoint, meeting-api and runtime sources (stock
 *                 dispatch), Compose, Helm and Lite. The exclusion block, and comments in the ignore files
 *                 that carry it, are skipped; so is each entry of `SANCTIONED`, a named mention with its
 *                 reason (the bot's manifest declares the vendor-neutral capture decoder the operator-run
 *                 probes resolve through it).
 *   logged        every native library a tracked `binding.gyp` links has a row in
 *                 `license-exceptions.json`: `operatorSupplied` for the optional runtime, `categoryB` for a
 *                 weak-copyleft library. An `operatorSupplied` row names the sealed contracts its
 *                 subprocess speaks, and each must be sealed in `contracts.seal.json`.
 *
 * The dockerignore half of `excluded` is proved through parity, not by a docker matcher: the three
 * blocks are byte-identical, git proves the semantics on the `.gitignore` copy, and the block uses only
 * the shape (`dir/*` then `!dir/file`) that git and BuildKit read the same way.
 */
import { readFileSync, existsSync } from "node:fs";
import { join, dirname, basename, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { devNull } from "node:os";
import { checkFact, loadManifest, MANIFEST_PATH as PARITY_MANIFEST } from "./check-parity.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = join(HERE, "..");

export const NATIVE_ROOT = "core/meetings/services/bot/runtime/native-meeting";
export const NATIVE_DIR = `${NATIVE_ROOT}/native`;
export const DENY_LINE = `${NATIVE_DIR}/*`;
export const PARITY_FACT_ID = "native-sdk-exclusion";
export const EXCLUSION_FILES = [".gitignore", ".dockerignore", "deploy/lite/Dockerfile.lite.dockerignore"];
export const BLOCK_RE = /^# >>> native-sdk-exclusion[^\n]*\n([\s\S]*?)^# <<< native-sdk-exclusion[^\n]*$/m;
export const EXCEPTIONS_FILE = "license-exceptions.json";
export const SEAL_FILE = "contracts.seal.json";

// A path is a native payload by its name alone: the check must hold before anyone opens the file.
export function isPayload(path) {
  const p = path.replace(/\\/g, "/");
  const name = basename(p);
  if (p.split("/").includes("qt_libs")) return "bundled Qt tree (qt_libs)";
  if (/^libmeetingsdk/i.test(name)) return "native meeting SDK library (libmeetingsdk*)";
  if (name === "zoom_sdk.h") return "native meeting SDK header (zoom_sdk.h)";
  if (extname(name) === ".node") return "compiled Node addon (.node)";
  if (/\.so(\.\d+)*$/.test(name)) return "shared object (.so)";
  if (/\.dylib$/i.test(name)) return "shared library (.dylib)";
  if (/\.dll$/i.test(name)) return "shared library (.dll)";
  if (/\.a$/.test(name)) return "static library (.a)";
  if (/\.(zip|tgz|tar|tar\.(gz|xz|bz2|zst)|7z|xz|gz)$/i.test(name) && (/zoom|meeting.?sdk/i.test(name) || p.startsWith(`${NATIVE_ROOT}/`)))
    return "native SDK archive";
  return null;
}

// Names that mean "the native path" wherever they appear in what a stock install builds or runs.
export const PATH_NEEDLES = /native-meeting|zoom_sdk_wrapper|zoom_wrapper|meetingsdk|zoom_meeting_sdk|zoom_sdk\.h|qt_libs|ZOOM_SDK_|@vexa\/zoom-sdk-capture|@vexa\/join\/node|sdk-join\.v1|sdk-capture\.v1/;
export const REFERENCE_SCOPES = [
  "core/meetings/services/bot/src/",
  "core/meetings/services/bot/package.json",
  "core/meetings/services/bot/Dockerfile",
  "core/meetings/services/bot/Dockerfile.mock",
  "core/meetings/services/bot/entrypoint.sh",
  "core/meetings/services/meeting-api/src/",
  "core/runtime/src/",
  "deploy/compose/",
  "deploy/helm/",
  "deploy/lite/",
];
// A mention of the native path in a stock file that is a reviewed decision, not wiring: the file, the
// one name it may carry, and why. Anything else in the file is still checked.
export const SANCTIONED = [
  { path: "core/meetings/services/bot/package.json", needle: "@vexa/zoom-sdk-capture",
    reason: "the vendor-neutral TypeScript decoder of sdk-capture.v1 frames, which the operator-run probes under native-meeting/ resolve through the bot package; it links nothing, and no stock entrypoint imports it (the bot's src/ is in scope)" },
  { path: "core/meetings/services/bot/package.json", needle: "runtime/native-meeting/test/*.test.mjs",
    reason: "the bot's test scripts run the native path's offline tests (its IPC and contracts, with a fake addon); they load no SDK and are no entrypoint" },
];
// What loads a native addon: a `require` of a `.node` file, of the wrapper, or of the addon path the
// operator configures, and `process.dlopen`.
export const ADDON_LOAD = /require\(\s*(?:process\.env\.ZOOM_SDK_ADDON|[^)]*(?:\.node|zoom_sdk_wrapper)[^)]*)\)|process\.dlopen\s*\(/;
const isCode = (p) => /\.(js|mjs|cjs|ts|tsx)$/.test(p) && !/(^|\/)(tests?|__tests__)\//.test(p) && !/\.(test|spec)\.[cm]?[jt]sx?$/.test(p);
// Names that mean "the payload itself, or the build of the wrapper", in a manifest that installs things.
export const PAYLOAD_NEEDLES = /meetingsdk|zoom_meeting_sdk|zoom_sdk_wrapper|zoom_sdk\.h|qt_libs|binding\.gyp|node-gyp/;
const isInstaller = (p) => /(^|\/)(package\.json|pyproject\.toml|Dockerfile[^/]*|Makefile[^/]*|[^/]*\.mk|[^/]*\.sh)$/.test(p) || p.startsWith(".github/workflows/");
// Prose about the path is not wiring of it.
const PROSE = /\.(md|mdx)$/;

// Paths git must ignore. The last two are names nobody listed: the reason the block denies by default.
export const PROBES = [
  `${NATIVE_DIR}/libmeetingsdk.so`,
  `${NATIVE_DIR}/h/zoom_sdk.h`,
  `${NATIVE_DIR}/qt_libs/Qt/lib/libQt5Core.so.5`,
  `${NATIVE_DIR}/build/Release/zoom_sdk_wrapper.node`,
  `${NATIVE_DIR}/sdk-6.7.2.7020-renamed/any-file-at-all`,
  `${NATIVE_DIR}/unlisted-name.bin`,
];

const git = (root, args) => execFileSync("git", ["-c", `core.excludesFile=${devNull}`, ...args], { cwd: root, stdio: "pipe" }).toString();

/** Exit 0 = ignored, 1 = not ignored; anything else is a failure of git itself. */
function gitIgnores(root, path) {
  try { git(root, ["check-ignore", "--no-index", "-q", path]); return true; }
  catch (e) { if (e.status === 1) return false; throw e; }
}

export function blockOf(text) {
  const all = [...text.matchAll(new RegExp(BLOCK_RE.source, "gm"))];
  return all.length === 1 ? { body: all[0][1], count: 1, index: all[0].index, end: all[0].index + all[0][0].length } : { count: all.length };
}
// The file's text with the exclusion block blanked (line numbers preserved).
function withoutBlock(text) {
  const b = blockOf(text);
  if (b.count !== 1) return text;
  return text.slice(0, b.index) + "\n".repeat(text.slice(b.index, b.end).split("\n").length - 1) + text.slice(b.end);
}

/** The native libraries a binding.gyp links: `-l<name>` and `pkg-config --libs <Name>`. */
export function linkedLibraries(gyp) {
  const libs = new Set();
  for (const m of gyp.matchAll(/(?:^|["\s])-l([A-Za-z0-9_.+-]+)/g)) libs.add(m[1]);
  for (const m of gyp.matchAll(/pkg-config\s+--libs\s+([A-Za-z0-9_.+-]+)/g)) libs.add(m[1]);
  return [...libs].sort();
}

export function checkVendorPayload(root = DEFAULT_ROOT) {
  const errs = [];
  try { git(root, ["rev-parse", "--is-inside-work-tree"]); }
  catch { throw new Error(`${root} is not a git checkout: gate:vendor-payload reads the tracked set from git`); }
  const tracked = git(root, ["ls-files", "-z"]).split("\0").filter(Boolean);
  const trackedSet = new Set(tracked);
  const read = (p) => { try { return readFileSync(join(root, p), "utf8"); } catch { return null; } };

  // tracked
  for (const p of tracked) {
    const why = isPayload(p);
    if (why) errs.push(`tracked: ${p} is a ${why}. Native payload is operator-supplied and never committed (P17, ADR-0039). \`git rm --cached\` it.`);
  }

  // excluded
  let reincluded = 0;
  for (const f of EXCLUSION_FILES) {
    const text = read(f);
    if (text === null) { errs.push(`excluded: ${f} does not exist`); continue; }
    const b = blockOf(text);
    if (b.count !== 1) { errs.push(`excluded: ${f} carries ${b.count} native-sdk-exclusion blocks; it must carry exactly one (\`# >>> native-sdk-exclusion\` … \`# <<< native-sdk-exclusion\`)`); continue; }
    const lines = b.body.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
    const deny = lines.indexOf(DENY_LINE);
    if (deny < 0) errs.push(`excluded: ${f}'s block does not deny by default under ${NATIVE_DIR}/ (missing \`${DENY_LINE}\`). A list of names admits an SDK unpacked under any other name (D-11).`);
    lines.forEach((l, i) => {
      if (!l.startsWith("!")) return;
      const p = l.slice(1);
      if (f === EXCLUSION_FILES[0]) reincluded++;
      if (!p.startsWith(`${NATIVE_DIR}/`)) errs.push(`excluded: ${f} re-includes ${p}, outside ${NATIVE_DIR}/. The block only ever narrows the native directory.`);
      if (/[*?[]/.test(p)) errs.push(`excluded: ${f} re-includes the pattern ${p}. Re-include named files only, never a glob.`);
      if (isPayload(p)) errs.push(`excluded: ${f} re-includes ${p}, which is a ${isPayload(p)}.`);
      else if (!trackedSet.has(p)) errs.push(`excluded: ${f} re-includes ${p}, which is not a tracked file. Re-include only Vexa-owned source that is committed.`);
      if (deny >= 0 && i < deny) errs.push(`excluded: ${f} re-includes ${p} before \`${DENY_LINE}\`, so the deny overrides it. Put the deny line first.`);
    });
  }
  const fact = (loadManifest(root).facts || []).find((x) => x.id === PARITY_FACT_ID);
  if (!fact) errs.push(`excluded: ${PARITY_MANIFEST} has no "${PARITY_FACT_ID}" fact. The three exclusion blocks are one fact and must be enforced as one.`);
  else {
    const sites = (fact.sites || []).map((s) => s.path).sort();
    if (!fact.enforced) errs.push(`excluded: "${PARITY_FACT_ID}" in ${PARITY_MANIFEST} is not enforced.`);
    if (JSON.stringify(sites) !== JSON.stringify([...EXCLUSION_FILES].sort()))
      errs.push(`excluded: "${PARITY_FACT_ID}" in ${PARITY_MANIFEST} must name exactly ${EXCLUSION_FILES.join(", ")}; it names ${sites.join(", ") || "nothing"}.`);
    for (const e of checkFact(fact, root).errs) errs.push(`excluded: ${e}`);
  }
  for (const p of PROBES.filter((p) => !gitIgnores(root, p)))
    errs.push(`excluded: git does not ignore ${p}. Everything under ${NATIVE_DIR}/ that is not re-included by name must be ignored.`);

  // fetched + unreferenced
  let scanned = 0, installers = 0;
  const inScope = (p) => REFERENCE_SCOPES.some((s) => (s.endsWith("/") ? p.startsWith(s) : p === s));
  for (const p of tracked) {
    const referenceScope = inScope(p) && !PROSE.test(p);
    const installer = isInstaller(p);
    if (!referenceScope && !installer) continue;
    let text = read(p);
    if (text === null) continue;
    // An ignore file's block and its comments are the one sanctioned mention: they exclude, not wire.
    const ignoreFile = EXCLUSION_FILES.includes(p);
    if (ignoreFile) text = withoutBlock(text);
    if (referenceScope) scanned++;
    if (installer) installers++;
    // A sanctioned mention is cut out of its line, and BOTH checks run on what is left: the mention
    // exempts itself and nothing else on the line, so a sanctioned test path chained to a wrapper
    // build (`… && node-gyp rebuild`) still fails `fetched`.
    const sanctioned = SANCTIONED.filter((s) => s.path === p).map((s) => s.needle);
    const sanctionedRe = sanctioned.length ? new RegExp(sanctioned.map((n) => n.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")).join("|"), "g") : null;
    text.split("\n").forEach((raw, i) => {
      if (ignoreFile && raw.trimStart().startsWith("#")) return;
      const line = sanctionedRe ? raw.replace(sanctionedRe, "") : raw;
      const ref = referenceScope && line.match(PATH_NEEDLES);
      if (ref) errs.push(`unreferenced: ${p}:${i + 1} names the native path (\`${ref[0]}\`). The optional runtime is off by default and absent from every stock entrypoint and deployment (P17, ADR-0039).`);
      const got = installer && line.match(PAYLOAD_NEEDLES);
      if (got) errs.push(`fetched: ${p}:${i + 1} names the native payload or its build (\`${got[0]}\`). Vexa never downloads, installs or compiles the optional runtime; the operator does (P17, ADR-0039).`);
    });
  }

  // reached: only the declared subprocess loads a native addon
  const subprocesses = new Set((() => { try { return (JSON.parse(read(EXCEPTIONS_FILE) || "{}").operatorSupplied || []).map((r) => r.subprocess).filter(Boolean); } catch { return []; } })());
  let loaders = 0;
  for (const p of tracked.filter(isCode)) {
    if (p.startsWith("scripts/check-vendor-payload")) continue;
    const text = read(p);
    if (text === null) continue;
    text.split("\n").forEach((line, i) => {
      if (!ADDON_LOAD.test(line)) return;
      if (subprocesses.has(p)) { loaders++; return; }
      errs.push(`reached: ${p}:${i + 1} loads a native addon (\`${line.match(ADDON_LOAD)[0]}\`). Only the declared subprocess may (${[...subprocesses].join(", ") || "none declared"}): the optional runtime is reached from a disposable child process only (P17, ADR-0039).`);
    });
  }

  // logged
  const exText = read(EXCEPTIONS_FILE);
  const exceptions = exText ? JSON.parse(exText) : {};
  const seal = (() => { const s = read(SEAL_FILE); return s ? JSON.parse(s) : {}; })();
  const rows = new Map();
  for (const r of exceptions.operatorSupplied || []) if (r.link) rows.set(r.link, { ...r, list: "operatorSupplied" });
  for (const r of exceptions.categoryB || []) if (r.link) rows.set(r.link, { ...r, list: "categoryB" });
  const linked = [];
  for (const p of tracked.filter((t) => basename(t) === "binding.gyp")) {
    for (const lib of linkedLibraries(read(p) || "")) {
      linked.push(lib);
      if (!rows.has(lib)) errs.push(`logged: ${p} links \`${lib}\`, which has no row in ${EXCEPTIONS_FILE} (\`operatorSupplied\` for an optional runtime, \`categoryB\` for weak copyleft). Every native library is a logged decision (P17).`);
    }
  }
  for (const r of exceptions.operatorSupplied || []) {
    const id = r.name || r.link || "(unnamed row)";
    for (const k of ["name", "link", "license", "reason", "contracts", "approved"])
      if (!r[k] || (Array.isArray(r[k]) && !r[k].length)) errs.push(`logged: operatorSupplied row "${id}" in ${EXCEPTIONS_FILE} has no \`${k}\`.`);
    for (const c of r.contracts || [])
      if (!seal[c]) errs.push(`logged: operatorSupplied row "${id}" names contract ${c}, which is not sealed in ${SEAL_FILE}. The optional runtime is reached only behind sealed contracts.`);
    if (r.subprocess && !trackedSet.has(r.subprocess))
      errs.push(`logged: operatorSupplied row "${id}" names subprocess ${r.subprocess}, which is not a tracked file.`);
  }

  return { errs, tracked: tracked.length, reincluded, probes: PROBES.length, scanned, installers, loaders, linked: [...new Set(linked)].sort(), operatorSupplied: (exceptions.operatorSupplied || []).length };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const r = checkVendorPayload();
  for (const e of r.errs) console.error("  ✗ " + e);
  if (r.errs.length) process.exit(1);
  console.log(`vendor-payload: ${r.tracked} tracked · ${r.probes} probes ignored · ${r.reincluded} re-included · ${r.scanned} stock files + ${r.installers} manifests clean · linked ${r.linked.join(", ") || "none"}, each logged`);
}
