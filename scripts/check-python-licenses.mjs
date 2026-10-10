#!/usr/bin/env node
/**
 * The Python half of gate:licenses (P17, ADR-0004): every Python package a Vexa image installs is
 * licence-classified, from the same lockfiles the images install from.
 *
 * WHAT SHIPS is read from the image recipes, not from a hand-kept list. Every tracked Dockerfile is
 * scanned in order: a `COPY …/uv.lock` names the project the next `uv sync` installs, and that sync's
 * flags name the dependency groups it adds. A sync that would install the default `dev` group (no
 * `--no-dev`, no `--no-default-groups`) is itself a failure, because test tooling would ship. A
 * `pip install` / `uv pip install` line names extra packages outside any lock; its resolved closure is
 * recorded in the index (below) and classified like the rest.
 *
 * WHAT A PACKAGE IS LICENSED UNDER cannot come from uv.lock, which carries no licence field. It comes
 * from `python-licenses.json`, an index of `name==version → SPDX` read from PyPI's metadata for the
 * exact locked version (`license_expression`, else a recognisable `license` field, else the licence
 * classifiers, joined with AND when there are several, which is the restrictive reading). The index is
 * data a human can review and correct: `--refresh` only fills keys that are missing, and never rewrites
 * a recorded one. A locked version with no row fails the gate, so a bump is re-read before it ships.
 *
 * LIMIT: a row is the licence the PACKAGE declares. Native libraries a binary wheel bundles (the
 * `<package>.libs/` directory auditwheel writes) can be under other licences, and nothing in the
 * package metadata says so. Those are recorded in NOTICE, not here: numpy's libgfortran and
 * libquadmath, and PyAV's FFmpeg build with libx264/libx265 in the transcription image.
 *
 * Classification is the one gates.mjs uses for npm (injected, so the two cannot drift): Category A
 * passes; Category B needs a `license-exceptions.json` categoryB row with `"ecosystem": "pypi"`;
 * Category X and anything unclassified fail.
 *
 * Usage:
 *   node scripts/check-python-licenses.mjs            list what each install line ships (classification: gate:licenses)
 *   node scripts/check-python-licenses.mjs --refresh  fill missing rows from PyPI (network) and resolve
 *                                                     each pip-install line with `uv pip compile`
 */
import { readFileSync, writeFileSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { join, dirname, posix } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = join(HERE, "..");
export const INDEX_FILE = "python-licenses.json";
export const EXCEPTIONS_FILE = "license-exceptions.json";
export const REFRESH = "node scripts/check-python-licenses.mjs --refresh";

// ── image recipes ───────────────────────────────────────────────────────────────────────────────

function trackedDockerfiles(root) {
  const out = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" });
  return out.split("\0").filter((p) => /(^|\/)Dockerfile[^/]*$/.test(p) && !/\.(md|dockerignore)$/.test(p)).sort();
}

// Docker's logical lines: comments dropped (also inside a continued RUN), backslash continuations joined.
export function logicalLines(text) {
  const lines = [];
  let cur = "";
  for (const raw of text.split(/\r?\n/)) {
    if (/^\s*#/.test(raw)) continue;
    if (/\\\s*$/.test(raw)) { cur += raw.replace(/\\\s*$/, " "); continue; }
    lines.push((cur + raw).trim());
    cur = "";
  }
  if (cur.trim()) lines.push(cur.trim());
  return lines.filter(Boolean);
}

export function shellWords(s) {
  const words = [];
  for (const m of s.matchAll(/"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g)) words.push(m[1] ?? m[2] ?? m[3]);
  return words;
}

const PIP_VALUE_FLAGS = new Set(["--python", "-p", "--index-url", "-i", "--extra-index-url", "--target", "-t",
  "--prefix", "--root", "--find-links", "-f", "--cache-dir", "--platform", "--python-version", "--python-platform"]);

// → [{ dockerfile, kind: "sync", lockDir, groups, installsDev } | { dockerfile, kind: "pip", lockDir, specs }]
export function pythonInstalls(dockerfile, text) {
  const out = [];
  let lockDir = null;
  for (const line of logicalLines(text)) {
    const [instr, ...rest] = line.split(/\s+/);
    if (/^COPY$/i.test(instr)) {
      const args = shellWords(rest.join(" ")).filter((a) => !a.startsWith("--"));
      for (const src of args.slice(0, -1)) {
        if (posix.basename(src) !== "uv.lock") continue;
        lockDir = src.includes("/") ? posix.dirname(src) : posix.dirname(dockerfile);
      }
      continue;
    }
    if (!/^RUN$/i.test(instr)) continue;
    for (const cmd of rest.join(" ").split(/&&|;|\|\|/)) {
      const sync = cmd.match(/\buv\s+sync\b(.*)$/);
      if (sync) {
        const w = shellWords(sync[1]);
        const groups = [];
        w.forEach((x, i) => { if (x === "--group") groups.push(w[i + 1]); else if (x.startsWith("--group=")) groups.push(x.slice(8)); });
        out.push({ dockerfile, kind: "sync", lockDir, groups,
          installsDev: !w.includes("--no-dev") && !w.includes("--no-default-groups") && !w.includes("--only-group"),
          allGroups: w.includes("--all-groups") });
        continue;
      }
      const pip = cmd.match(/\b(?:uv\s+)?pip3?\s+install\b(.*)$/);
      if (pip) {
        const w = shellWords(pip[1]);
        const specs = [];
        for (let i = 0; i < w.length; i++) {
          if (PIP_VALUE_FLAGS.has(w[i])) { i++; continue; }
          if (w[i].startsWith("-")) continue;
          specs.push(w[i]);
        }
        out.push({ dockerfile, kind: "pip", lockDir, specs, requirementFile: w.includes("-r") || w.includes("--requirement") });
      }
    }
  }
  return out;
}

export const installKey = (lockDir, specs) => `${lockDir || "-"} :: ${[...specs].sort().join(" ")}`;

// ── uv.lock ─────────────────────────────────────────────────────────────────────────────────────

function depsOf(arrayText) {
  const deps = [];
  for (const m of arrayText.matchAll(/\{(?:[^{}]|\{[^{}]*\})*\}/g)) {
    const e = m[0];
    const name = e.match(/\bname = "([^"]+)"/)?.[1];
    if (!name) continue;
    const extra = e.match(/\bextra = \[([^\]]*)\]/)?.[1];
    deps.push({
      name,
      version: e.match(/\bversion = "([^"]+)"/)?.[1] ?? null,
      extras: extra ? [...extra.matchAll(/"([^"]+)"/g)].map((x) => x[1]) : [],
      marker: e.match(/\bmarker = "((?:[^"\\]|\\.)*)"/)?.[1]?.replace(/\\"/g, '"') ?? null,
    });
  }
  return deps;
}

// The subset of uv.lock this gate needs: each package's name, version, whether it is the project
// itself, its dependencies, its extras' dependencies, and (for the project) its dependency groups.
export function parseUvLock(text) {
  const pkgs = [];
  let cur = null, section = null, arrayKey = null, buf = "";
  const flush = () => {
    const deps = depsOf(buf);
    if (section === "pkg" && arrayKey === "dependencies") cur.deps = deps;
    else if (section === "optional") cur.optional[arrayKey] = deps;
    else if (section === "dev") cur.groups[arrayKey] = deps;
    arrayKey = null; buf = "";
  };
  for (const line of text.split(/\r?\n/)) {
    if (arrayKey !== null) {
      buf += line + "\n";
      if (/^\s*\]\s*$/.test(line)) flush();
      continue;
    }
    if (line === "[[package]]") { cur = { name: null, version: null, root: false, deps: [], optional: {}, groups: {} }; pkgs.push(cur); section = "pkg"; continue; }
    if (line.startsWith("[")) {
      section = line === "[package.optional-dependencies]" ? "optional" : line === "[package.dev-dependencies]" ? "dev" : "other";
      if (!cur) section = "other";
      continue;
    }
    if (!cur || section === "other") continue;
    let m;
    if (section === "pkg" && (m = line.match(/^name = "([^"]+)"$/))) cur.name = m[1];
    else if (section === "pkg" && (m = line.match(/^version = "([^"]+)"$/))) cur.version = m[1];
    else if (section === "pkg" && /^source = \{ (virtual|editable) = "\." \}/.test(line)) cur.root = true;
    else if ((m = line.match(/^([A-Za-z0-9_.-]+) = \[(.*)$/)) && (section !== "pkg" || m[1] === "dependencies")) {
      arrayKey = m[1]; buf = m[2] + "\n";
      if (/\]\s*$/.test(m[2])) flush();
    }
  }
  return pkgs;
}

// ── markers: evaluated for the Linux CPython the images run, kept if ANY target could install it ──

const TARGETS = [];
for (const py of ["3.11.13", "3.12.12", "3.13.7"]) for (const machine of ["x86_64", "aarch64"]) TARGETS.push({
  python_version: py.split(".").slice(0, 2).join("."), python_full_version: py, sys_platform: "linux",
  platform_system: "Linux", platform_machine: machine, platform_python_implementation: "CPython",
  implementation_name: "cpython", os_name: "posix",
});
const VERSION_VARS = new Set(["python_version", "python_full_version"]);
const vcmp = (a, b) => {
  const pa = a.split(".").map((x) => parseInt(x, 10) || 0), pb = b.split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d; }
  return 0;
};
function compare(env, left, op, right) {
  const lv = left.var ? env[left.var] : left.str, rv = right.var ? env[right.var] : right.str;
  if (lv === undefined || rv === undefined) return true;                     // unknown variable: keep the dep
  const isVersion = VERSION_VARS.has(left.var) || VERSION_VARS.has(right.var);
  switch (op) {
    case "==": return isVersion ? vcmp(lv, rv) === 0 : lv === rv;
    case "!=": return isVersion ? vcmp(lv, rv) !== 0 : lv !== rv;
    case "<": return vcmp(lv, rv) < 0;
    case "<=": return vcmp(lv, rv) <= 0;
    case ">": return vcmp(lv, rv) > 0;
    case ">=": return vcmp(lv, rv) >= 0;
    case "in": return rv.includes(lv);
    case "not in": return !rv.includes(lv);
    default: return true;
  }
}
export function markerApplies(marker) {
  if (!marker) return true;
  const toks = [...marker.matchAll(/\s*(\(|\)|not in\b|==|!=|<=|>=|<|>|in\b|and\b|or\b|'[^']*'|"[^"]*"|[A-Za-z_][A-Za-z0-9_.]*)/g)].map((m) => m[1]);
  return TARGETS.some((env) => {
    let i = 0;
    const atom = () => {
      if (toks[i] === "(") { i++; const v = or(); i++; return v; }
      const term = (t) => (/^['"]/.test(t) ? { str: t.slice(1, -1) } : { var: t });
      const left = term(toks[i++]); const op = toks[i++]; const right = term(toks[i++]);
      return compare(env, left, op, right);
    };
    const and = () => { let v = atom(); while (toks[i] === "and") { i++; const r = atom(); v = v && r; } return v; };
    const or = () => { let v = and(); while (toks[i] === "or") { i++; const r = and(); v = v || r; } return v; };
    try { return or(); } catch { return true; }
  });
}

// name==version of every package the project's install pulls in (the project itself excluded).
export function lockClosure(pkgs, groups = []) {
  const root = pkgs.find((p) => p.root);
  if (!root) throw new Error("no project package (source virtual/editable) in the lock");
  const byName = new Map();
  for (const p of pkgs) { if (!byName.has(p.name)) byName.set(p.name, []); byName.get(p.name).push(p); }
  const start = [...root.deps];
  for (const g of groups) {
    if (!root.groups[g]) throw new Error(`dependency group "${g}" is not in the lock`);
    start.push(...root.groups[g]);
  }
  const seen = new Set(), queue = [...start], out = new Set();
  while (queue.length) {
    const d = queue.shift();
    if (!markerApplies(d.marker)) continue;
    const cands = (byName.get(d.name) || []).filter((p) => !p.root && (!d.version || p.version === d.version));
    if (!cands.length) throw new Error(`"${d.name}" is required but not locked`);
    for (const p of cands) {
      const key = `${p.name}==${p.version}`;
      const visit = `${key}[${d.extras.join(",")}]`;
      if (seen.has(visit)) continue;
      seen.add(visit);
      out.add(key);
      queue.push(...p.deps);
      for (const x of d.extras) queue.push(...(p.optional[x] || []));
    }
  }
  return out;
}

// ── the check ───────────────────────────────────────────────────────────────────────────────────

const readJson = (root, f, dflt) => (existsSync(join(root, f)) ? JSON.parse(readFileSync(join(root, f), "utf8")) : dflt);

// What ships: [{ where, packages: Set<name==version> }], plus recipe errors.
export function shippedPython(root = DEFAULT_ROOT, index = readJson(root, INDEX_FILE, {})) {
  const errs = [], units = [], needed = new Set(), pipKeys = [];
  for (const df of trackedDockerfiles(root)) {
    for (const ins of pythonInstalls(df, readFileSync(join(root, df), "utf8"))) {
      if (ins.kind === "sync") {
        if (!ins.lockDir) { errs.push(`${df}: \`uv sync\` with no \`COPY …/uv.lock\` before it — this gate cannot tell what it installs`); continue; }
        if (ins.installsDev) errs.push(`${df}: \`uv sync\` for ${ins.lockDir} installs the default dev group — add --no-dev (test tooling must not ship)`);
        if (ins.allGroups) errs.push(`${df}: \`uv sync --all-groups\` for ${ins.lockDir} ships every dependency group — name the groups the image needs`);
        let pkgs;
        try { pkgs = lockClosure(parseUvLock(readFileSync(join(root, ins.lockDir, "uv.lock"), "utf8")), ins.groups); }
        catch (e) { errs.push(`${df}: ${ins.lockDir}/uv.lock — ${e.message}`); continue; }
        pkgs.forEach((k) => needed.add(k));
        units.push({ dockerfile: df, where: `${df} (${ins.lockDir}${ins.groups.length ? ` +${ins.groups.join(",")}` : ""})`, packages: pkgs });
      } else {
        if (ins.requirementFile) { errs.push(`${df}: \`pip install -r\` — name the packages on the line so this gate can classify them`); continue; }
        if (!ins.specs.length) continue;
        const key = installKey(ins.lockDir, ins.specs);
        pipKeys.push({ key, ins });
        const resolved = index.installs?.[key];
        if (!resolved) { errs.push(`${df}: \`pip install ${ins.specs.join(" ")}\` has no resolved closure in ${INDEX_FILE} — run \`${REFRESH}\``); continue; }
        const pkgs = new Set(resolved);
        pkgs.forEach((k) => needed.add(k));
        units.push({ dockerfile: df, where: `${df} (pip install ${ins.specs.join(" ")})`, packages: pkgs });
      }
    }
  }
  return { errs, units, needed, pipKeys };
}

// classifyLicense: the gates.mjs classifier → "A" | "B" | "X" | "?"
export function checkPythonLicenses(root = DEFAULT_ROOT, classifyLicense) {
  const index = readJson(root, INDEX_FILE, null);
  if (!index) return { errs: [`${INDEX_FILE} is missing — run \`${REFRESH}\``], flagged: [], total: 0, installs: 0, dockerfiles: 0 };
  const exceptions = (readJson(root, EXCEPTIONS_FILE, {}).categoryB || []).filter((e) => e.ecosystem === "pypi");
  const { errs, units, needed } = shippedPython(root, index);
  const byLicense = new Map();
  for (const key of [...needed].sort()) {
    const lic = index.licenses?.[key];
    if (!lic) { errs.push(`${key} ships but has no licence row in ${INDEX_FILE} — run \`${REFRESH}\`, then review the row`); continue; }
    if (!byLicense.has(lic)) byLicense.set(lic, []);
    byLicense.get(lic).push(key);
  }
  const flagged = [];
  for (const [lic, keys] of byLicense) {
    const cat = classifyLicense(lic);
    if (cat === "A") continue;
    const names = [...new Set(keys.map((k) => k.split("==")[0]))];
    if (cat === "B") {
      const unlisted = names.filter((n) => !exceptions.some((e) => n === e.package));
      if (unlisted.length) errs.push(`Cat-B ${lic} needs a license-exceptions.json categoryB row with "ecosystem": "pypi": ${unlisted.join(", ")}`);
      else flagged.push(`${lic} (${names.join(", ")})`);
    } else if (cat === "X") errs.push(`FORBIDDEN (Cat X) ${lic}: ${keys.join(", ")} — replace this dependency`);
    else errs.push(`unclassified licence "${lic}": ${keys.join(", ")} — correct the row in ${INDEX_FILE} or classify it in scripts/gates.mjs`);
  }
  return { errs, flagged, total: needed.size, installs: units.length, dockerfiles: new Set(units.map((u) => u.dockerfile)).size };
}

// ── --refresh: fill missing rows from PyPI ──────────────────────────────────────────────────────

const PHRASES = new Map(Object.entries({
  "mit": "MIT", "mit license": "MIT", "the mit license": "MIT", "mit no attribution": "MIT-0",
  "bsd": "BSD", "bsd license": "BSD", "new bsd license": "BSD-3-Clause", "bsd 3-clause license": "BSD-3-Clause",
  "bsd-3-clause license": "BSD-3-Clause", "3-clause bsd license": "BSD-3-Clause", "bsd 2-clause license": "BSD-2-Clause",
  "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0", "apache 2": "Apache-2.0", "apache license 2.0": "Apache-2.0",
  "apache license, version 2.0": "Apache-2.0", "apache license version 2.0": "Apache-2.0",
  "apache software license": "Apache-2.0", "apache software license 2.0": "Apache-2.0",
  "psf": "Python-2.0", "psf license": "Python-2.0", "python software foundation license": "Python-2.0",
  "isc": "ISC", "isc license": "ISC", "isc license (iscl)": "ISC", "mpl 2.0": "MPL-2.0", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
}));
const CLASSIFIERS = new Map(Object.entries({
  "MIT License": "MIT", "MIT No Attribution License (MIT-0)": "MIT-0", "BSD License": "BSD",
  "Apache Software License": "Apache-2.0", "Python Software Foundation License": "Python-2.0", "ISC License (ISCL)": "ISC",
  "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
  "Historical Permission Notice and Disclaimer (HPND)": "HPND", "Zope Public License": "ZPL-2.1",
  "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only", "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
  "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later", "GNU Library or Lesser General Public License (LGPL)": "LGPL",
  "GNU General Public License v2 (GPLv2)": "GPL-2.0-only", "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
  "GNU Affero General Public License v3": "AGPL-3.0-only",
}));
const SPDX_EXPR = /^\(?[A-Za-z0-9.+-]+(?:\s+(?:AND|OR|WITH)\s+\(?[A-Za-z0-9.+-]+\)?)*\)?$/;

export function spdxFromPyPI(info) {
  if (info.license_expression) return info.license_expression.trim();
  const lic = (info.license || "").trim();
  if (lic && !lic.includes("\n") && lic.length <= 80) {
    const phrase = PHRASES.get(lic.toLowerCase());
    if (phrase) return phrase;
    if (SPDX_EXPR.test(lic) && /\d|^MIT|^ISC|^BSD/.test(lic)) return lic;
  }
  const cls = (info.classifiers || []).filter((c) => c.startsWith("License :: ")).map((c) => c.split(" :: ").pop());
  const mapped = [...new Set(cls.map((c) => CLASSIFIERS.get(c)).filter(Boolean))];
  if (mapped.length && mapped.length === new Set(cls).size) return mapped.join(" AND ");
  return `UNKNOWN (license=${JSON.stringify(lic.slice(0, 60))}; classifiers=${JSON.stringify(cls)})`;
}

function resolvePip(root, lockDir, specs) {
  const dir = mkdtempSync(join(tmpdir(), "pylic-"));
  try {
    writeFileSync(join(dir, "req.in"), specs.join("\n") + "\n");
    const args = ["pip", "compile", join(dir, "req.in"), "--python-version", "3.12", "--python-platform", "x86_64-manylinux_2_28",
      "--no-header", "--no-annotate", "--quiet"];
    if (lockDir) {
      // The image installs these INTO a venv already synced from the lock, and uv keeps an installed
      // version that satisfies them. Constraining to the lock's production closure reproduces that.
      const pinned = [...lockClosure(parseUvLock(readFileSync(join(root, lockDir, "uv.lock"), "utf8")))];
      writeFileSync(join(dir, "c.txt"), pinned.join("\n") + "\n");
      try { return compile([...args, "-c", join(dir, "c.txt")]); } catch { /* conflicts with the lock: resolve alone */ }
    }
    return compile(args);
  } finally { rmSync(dir, { recursive: true, force: true }); }
  function compile(a) {
    return execFileSync("uv", a, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
      .split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"))
      .map((l) => l.replace(/\s*;.*$/, "").replace(/\[[^\]]*\]/, ""))
      .map((l) => { const [name, version] = l.split("=="); return `${name.trim().toLowerCase().replace(/[-_.]+/g, "-")}==${version.trim()}`; });
  }
}

async function refresh(root) {
  const index = readJson(root, INDEX_FILE, { licenses: {}, installs: {} });
  index.licenses ||= {}; index.installs ||= {};
  const { pipKeys } = shippedPython(root, index);
  for (const { key, ins } of pipKeys) if (!index.installs[key]) index.installs[key] = resolvePip(root, ins.lockDir, ins.specs).sort();
  const { needed } = shippedPython(root, index);
  const missing = [...needed].filter((k) => !index.licenses[k]).sort();
  for (const key of missing) {
    const [name, version] = key.split("==");
    const res = await fetch(`https://pypi.org/pypi/${encodeURIComponent(name)}/${encodeURIComponent(version)}/json`);
    index.licenses[key] = res.ok ? spdxFromPyPI((await res.json()).info) : `UNKNOWN (PyPI ${res.status})`;
    console.log(`  + ${key}: ${index.licenses[key]}`);
  }
  const sorted = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
  const out = { _comment: index._comment, licenses: sorted(index.licenses), installs: sorted(index.installs) };
  writeFileSync(join(root, INDEX_FILE), JSON.stringify(out, null, 2) + "\n");
  console.log(`${INDEX_FILE}: ${missing.length} row(s) added, ${Object.keys(out.licenses).length} total`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const root = process.cwd();
  if (process.argv.includes("--refresh")) await refresh(root);
  else {
    // What ships, per install line. Classification is gate:licenses (it owns the classifier).
    const r = shippedPython(root);
    for (const u of r.units) console.log(`${u.where}: ${u.packages.size} package(s)`);
    for (const e of r.errs) console.error(`  ✗ ${e}`);
    process.exit(r.errs.length ? 1 : 0);
  }
}
