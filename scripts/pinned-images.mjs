#!/usr/bin/env node
/**
 * Every third-party container image a Vexa deploy surface pins: the one list that gate:image-licenses
 * audits for licence (image-licenses.json) and that .github/workflows/cve-scanning.yml scans for CVEs.
 * Reading both from here is what keeps the scanned set and the audited set from drifting apart.
 *
 * Forms read:
 *   • scalar `image: ref` in compose, Helm values and Helm templates (`${VAR:-default}` → default);
 *   • structured Helm `image: { repository, tag }` blocks;
 *   • every `FROM ref` in the Lite Dockerfile (builder stages too: their bytes feed the final image);
 *   • non-recipe `*_IMAGE` variable assignments in the Lite Makefile.
 * Our own vexaai/* and vexa/* images are built here, not third-party inputs, and are skipped.
 *
 * Usage: node scripts/pinned-images.mjs --json   → ["ref", …] (every distinct ref, sorted)
 */
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// → Map name → { ref, source, refs: Set<ref> }   (ref/source = first sighting; refs = every one)
export function collectPinnedImages(root = DEFAULT_ROOT) {
  const rel = (p) => p.slice(root.length + 1);
  const chartDir = join(root, "deploy", "helm", "charts", "vexa");
  const tplDir = join(chartDir, "templates");
  const helmValueFiles = existsSync(chartDir)
    ? readdirSync(chartDir).filter((f) => /^values.*\.yaml$/.test(f)).map((f) => join(chartDir, f))
    : [];
  const deployFiles = [
    join(root, "deploy", "compose", "docker-compose.yml"),
    ...helmValueFiles,
    ...(existsSync(tplDir) ? readdirSync(tplDir).filter((f) => /\.ya?ml$/.test(f)).map((f) => join(tplDir, f)) : []),
  ].filter(existsSync);
  const found = new Map();
  const recordImage = (rawRef, source) => {
    let ref = rawRef.replace(/^["']|["']$/g, "");
    if (ref.includes("{{")) return;                                          // helm template expression
    ref = ref.replace(/\$\{[^:}]*:-([^}]+)\}/g, "$1");                       // ${VAR:-default} → default image
    if (ref.includes("$")) return;                                           // unresolved variable → no auditable pin
    if (ref.startsWith("vexaai/") || ref.startsWith("vexa/")) return;         // first-party
    const name = ref.replace(/@sha256:.*$/, "").replace(/:[^/:]+$/, "");     // strip digest / :tag
    if (!name || (!ref.includes("/") && !ref.includes(":"))) return;         // Docker stage alias / invalid ref
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

  const liteDockerfile = join(root, "deploy", "lite", "Dockerfile.lite");
  if (existsSync(liteDockerfile))
    for (const m of readFileSync(liteDockerfile, "utf8").matchAll(/^FROM[ \t]+(?:--platform=\S+[ \t]+)?(\S+)/gmi))
      recordImage(m[1], rel(liteDockerfile));

  const liteMakefile = join(root, "deploy", "lite", "Makefile");
  if (existsSync(liteMakefile)) {
    // ?=, :=, ::=, = with optional export/override; not target-specific assignments, define blocks or \-continued values.
    for (const line of readFileSync(liteMakefile, "utf8").split(/\r?\n/)) {
      if (line.startsWith("\t")) continue;
      const assignment = line.match(/^[ \t]*(?:(?:export|override)[ \t]+)*[A-Za-z_][A-Za-z0-9_]*_IMAGE[ \t]*(?:\?=|::?=|=)[ \t]*([^\s#]+)/);
      if (assignment) recordImage(/[/@:$]/.test(assignment[1]) ? assignment[1] : `${assignment[1]}:latest`, rel(liteMakefile));
    }
  }
  return found;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const refs = [...collectPinnedImages(process.cwd()).values()].flatMap((e) => [...e.refs]).sort();
  if (process.argv.includes("--json")) console.log(JSON.stringify(refs));
  else for (const r of refs) console.log(r);
}
