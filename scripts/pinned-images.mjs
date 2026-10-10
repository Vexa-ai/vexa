#!/usr/bin/env node
/**
 * Every third-party container image a Vexa deploy surface pins: the one list that gate:image-licenses
 * audits for licence (image-licenses.json) and that .github/workflows/cve-scanning.yml scans for CVEs.
 * Reading both from here is what keeps the scanned set and the audited set from drifting apart.
 *
 * Where it looks, discovered rather than listed:
 *   • every compose file under deploy/ (any tracked `*compose*.yml`: the stack, its overlays, the
 *     transcription stack) and every Helm chart under deploy/helm/charts (values*.yaml and templates);
 *   • every tracked Dockerfile (`Dockerfile`, `Dockerfile.*`, `*.Dockerfile`), all stages: a builder
 *     stage's bytes feed the final image, and a base image is an input like any other;
 *   • `docker run` lines in the shell scripts and Makefiles under deploy/ (the dogfood rig's mail
 *     catcher), and non-recipe `*_IMAGE` variable assignments in those Makefiles.
 * Forms read: scalar `image: ref` (`${VAR:-default}` → default); structured Helm
 * `image: { repository, tag }` blocks; `FROM ref` with `ARG NAME=default` substituted; the image
 * operand of `docker run`. A tree under a directory carrying `.gateignore` is skipped, as every per-dir
 * gate skips it (the retiring dashboard, which this line does not build). Our own vexaai/* and vexa/*
 * images are built here, not third-party inputs, and are skipped.
 *
 * Usage: node scripts/pinned-images.mjs --json   → ["ref", …] (every distinct ref, sorted)
 */
import { readdirSync, readFileSync, existsSync, realpathSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const DEFAULT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// Tracked files, repo-relative, minus any under a `.gateignore` directory. Outside a git checkout
// (a bare copy) every file on disk counts.
function trackedFiles(root) {
  let files;
  try {
    files = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8", maxBuffer: 64 << 20 })
      .split("\0").filter(Boolean);
  } catch {
    files = [];
    const walk = (d) => {
      for (const n of readdirSync(join(root, d), { withFileTypes: true })) {
        if (n.name === "node_modules" || n.name === ".git") continue;
        const r = d ? `${d}/${n.name}` : n.name;
        if (n.isDirectory()) walk(r); else files.push(r);
      }
    };
    walk("");
  }
  const ignored = new Map();
  const underIgnore = (rel) => {
    for (let d = dirname(rel); d && d !== "."; d = dirname(d)) {
      if (!ignored.has(d)) ignored.set(d, existsSync(join(root, d, ".gateignore")));
      if (ignored.get(d)) return true;
    }
    return false;
  };
  return files.filter((f) => existsSync(join(root, f)) && !underIgnore(f));
}

const isDockerfile = (f) => /^Dockerfile(\.[^/]+)?$/.test(basename(f)) && !/\.(md|dockerignore)$/.test(f)
  || /\.Dockerfile$/.test(f);

// The `docker run` image operand: the first argument that is not an option or an option's value.
const RUN_VALUE_FLAGS = new Set(["--name", "--network", "--net", "-p", "--publish", "-e", "--env", "--env-file",
  "-v", "--volume", "--mount", "--restart", "-w", "--workdir", "-u", "--user", "--entrypoint", "-l", "--label",
  "--platform", "--cpus", "-m", "--memory", "--add-host", "-h", "--hostname", "--log-driver", "--log-opt",
  "--tmpfs", "--device", "--cap-add", "--cap-drop", "--security-opt", "--pull", "--ulimit", "--shm-size",
  "--stop-signal", "--health-cmd", "--ipc", "--pid", "--userns", "--dns", "--expose", "--gpus"]);
function dockerRunImage(command) {
  const toks = command.replace(/\\\n/g, " ").split(/\s+/).filter(Boolean);
  const at = toks.findIndex((t, i) => t === "run" && toks[i - 1] === "docker");
  if (at < 0) return null;
  for (let i = at + 1; i < toks.length; i++) {
    const t = toks[i];
    if (t.startsWith("-")) { if (!t.includes("=") && RUN_VALUE_FLAGS.has(t)) i++; continue; }
    return t;
  }
  return null;
}

// → Map name → { ref, source, refs: Set<ref> }   (ref/source = first sighting; refs = every one)
export function collectPinnedImages(root = DEFAULT_ROOT) {
  const rel = (p) => p.slice(root.length + 1);
  const files = trackedFiles(root);
  const deployDir = (f) => f.startsWith("deploy/");
  const chartFiles = files.filter((f) => /^deploy\/helm\/charts\/[^/]+\//.test(f));
  const helmValueFiles = chartFiles.filter((f) => /^deploy\/helm\/charts\/[^/]+\/values[^/]*\.ya?ml$/.test(f))
    .map((f) => join(root, f));
  const deployFiles = [
    ...files.filter((f) => deployDir(f) && /compose[^/]*\.ya?ml$/i.test(basename(f))),
    ...chartFiles.filter((f) => /^deploy\/helm\/charts\/[^/]+\/(values[^/]*|templates\/[^/]+)\.ya?ml$/.test(f)),
  ].map((f) => join(root, f));
  const found = new Map();
  // Images this repository BUILDS (`docker build … -t name:tag` in a workflow, Makefile or script) are
  // first-party, like vexaai/*: the compose Makefile's BROWSER_IMAGE default, mock-bot:dev, is one.
  const builtHere = new Set();
  for (const f of files.filter((x) => /^\.github\/workflows\/[^/]+\.ya?ml$/.test(x) || /(^|\/)(Makefile|[^/]+\.mk|[^/]+\.sh)$/.test(x))) {
    for (const m of readFileSync(join(root, f), "utf8").matchAll(/docker[ \t]+(?:buildx[ \t]+)?build\b[^\n]*?(?:-t|--tag)[ \t=]+["']?([^\s"']+)/g))
      if (!m[1].includes("$")) builtHere.add(m[1].replace(/@sha256:.*$/, "").replace(/:[^/:]+$/, ""));
  }
  const recordImage = (rawRef, source) => {
    let ref = rawRef.replace(/^["']|["']$/g, "");
    if (ref.includes("{{")) return;                                          // helm template expression
    ref = ref.replace(/\$\{[^:}]*:-([^}]+)\}/g, "$1");                       // ${VAR:-default} → default image
    if (ref.includes("$")) return;                                           // unresolved variable → no auditable pin
    if (ref.startsWith("vexaai/") || ref.startsWith("vexa/")) return;         // first-party
    const name = ref.replace(/@sha256:.*$/, "").replace(/:[^/:]+$/, "");     // strip digest / :tag
    if (!name || (!ref.includes("/") && !ref.includes(":"))) return;         // Docker stage alias / invalid ref
    if (builtHere.has(name)) return;                                         // built in this repository
    if (!found.has(name)) found.set(name, { ref, source, refs: new Set() });
    found.get(name).refs.add(ref);
  };

  for (const f of deployFiles)
    // same-line values only ([ \t]*, never \s* which would cross a newline into a structured
    // `image:\n  repository:` block — that shape is enumerated separately below). The value may carry
    // `${VAR:-default}` interpolation ⇒ capture the whole token (no `{}` exclusion) and resolve.
    for (const m of readFileSync(f, "utf8").matchAll(/^[ \t]*image:[ \t]*["']?([^\s"']+)/gm)) recordImage(m[1], rel(f));

  // Helm's structured values shape (image: / repository: / tag:), bound by indentation.
  for (const f of helmValueFiles) {
    const lines = readFileSync(f, "utf8").split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const image = lines[i].match(/^([ \t]*)image:[ \t]*(?:#.*)?$/);
      if (!image) continue;
      const baseIndent = image[1].length;
      let repository, tag;
      for (let j = i + 1; j < lines.length; j++) {
        if (!lines[j].trim() || lines[j].trimStart().startsWith("#")) continue;
        const indent = (lines[j].match(/^[ \t]*/) || [""])[0].length;
        if (indent <= baseIndent) break;
        const repoMatch = lines[j].match(/^[ \t]*repository:[ \t]*["']?([^\s"']+)/);
        const tagMatch = lines[j].match(/^[ \t]*tag:[ \t]*["']?([^\s"']+)/);
        if (repoMatch) repository = repoMatch[1];
        if (tagMatch) tag = tagMatch[1];
      }
      if (repository && tag) recordImage(`${repository}:${tag}`, rel(f));
    }
  }

  // Every Dockerfile's FROM, each stage, with the file's `ARG NAME=default` values substituted.
  for (const f of files.filter(isDockerfile)) {
    const text = readFileSync(join(root, f), "utf8");
    const args = new Map();
    for (const line of text.split(/\r?\n/)) {
      const arg = line.match(/^[ \t]*ARG[ \t]+([A-Za-z_][A-Za-z0-9_]*)=["']?([^\s"']*)/i);
      if (arg) { args.set(arg[1], arg[2]); continue; }
      const from = line.match(/^[ \t]*FROM[ \t]+(?:--platform=\S+[ \t]+)?(\S+)/i);
      if (from) recordImage(from[1].replace(/\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?/g, (m, n) => (args.has(n) ? args.get(n) : m)), f);
    }
  }

  const scripts = files.filter((f) => deployDir(f) && (/\.sh$/.test(f) || basename(f) === "Makefile" || /\.mk$/.test(f)));
  for (const f of scripts) {
    const text = readFileSync(join(root, f), "utf8");
    // `docker run … image`, continuation lines joined.
    for (const m of text.matchAll(/docker[ \t]+run\b(?:[^\n]*\\\n)*[^\n]*/g)) {
      const image = dockerRunImage(m[0]);
      if (image) recordImage(image, f);
    }
    if (!(basename(f) === "Makefile" || /\.mk$/.test(f))) continue;
    // ?=, :=, ::=, = with optional export/override; not target-specific assignments, define blocks or \-continued values.
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith("\t")) continue;
      const assignment = line.match(/^[ \t]*(?:(?:export|override)[ \t]+)*[A-Za-z_][A-Za-z0-9_]*_IMAGE[ \t]*(?:\?=|::?=|=)[ \t]*([^\s#]+)/);
      if (assignment) recordImage(/[/@:$]/.test(assignment[1]) ? assignment[1] : `${assignment[1]}:latest`, f);
    }
  }
  return found;
}

// Compared by real path: a temporary checkout under a symlinked directory (macOS's /var) would
// otherwise never run as a script.
if (process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1])) {
  const refs = [...collectPinnedImages(process.cwd()).values()].flatMap((e) => [...e.refs]).sort();
  if (process.argv.includes("--json")) console.log(JSON.stringify(refs));
  else for (const r of refs) console.log(r);
}
