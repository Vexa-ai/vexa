#!/usr/bin/env node
/* eslint-disable no-console */

/**
 * Sync Vexa markdown docs (docs/) into a Mintlify-compatible site folder (docs-site/).
 *
 * We keep `docs/` as the canonical source of truth.
 * `docs-site/` is the Mintlify publishing bundle: pages + docs.json.
 *
 * This script:
 * - copies all .md files from docs/ into docs-site/ as .mdx
 * - adds frontmatter (`title`, `description`) based on the first H1 and first paragraph
 * - preserves subfolders (e.g., docs/platforms/* -> docs-site/platforms/*)
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC_DIR = path.join(ROOT, "docs");
const OUT_DIR = path.join(ROOT, "docs-site");

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function emptyDir(p) {
  // Preserve mintlify config files if they exist.
  // We only want to regenerate the pages, not wipe docs.json.
  ensureDir(p);
  for (const ent of fs.readdirSync(p, { withFileTypes: true })) {
    if (ent.name === "docs.json" || ent.name === "README.md") continue;
    fs.rmSync(path.join(p, ent.name), { recursive: true, force: true });
  }
}

function walk(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...walk(abs));
    else out.push(abs);
  }
  return out;
}

function readUtf8(p) {
  return fs.readFileSync(p, "utf8");
}

function firstMatch(re, s) {
  const m = s.match(re);
  return m ? m[1].trim() : "";
}

function stripMd(s) {
  return s
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/#+\s+/g, "")
    .trim();
}

function buildFrontmatter(md) {
  const title = stripMd(firstMatch(/^#\s+(.+)\s*$/m, md)) || "Vexa Docs";

  // Crude description: first non-empty paragraph after H1.
  const afterH1 = md.split(/^#\s+.+\s*$/m).slice(1).join("\n");
  const para = stripMd(
    (afterH1 || "")
      .split("\n\n")
      .map((p) => p.trim())
      .find((p) => p && !p.startsWith(">") && !p.startsWith("```") && !p.startsWith("!["))
      || ""
  );
  const description = para ? para.slice(0, 160) : "";

  const lines = ["---", `title: "${title.replace(/"/g, '\\"')}"`];
  if (description) lines.push(`description: "${description.replace(/"/g, '\\"')}"`);
  lines.push("---", "");
  return lines.join("\n");
}

function hasFrontmatter(md) {
  return md.startsWith("---\n");
}

function relToDocs(p) {
  return path.relative(SRC_DIR, p).split(path.sep).join("/");
}

function toOutPath(rel) {
  // Convert docs/README.md -> docs-site/index.mdx
  if (rel.toLowerCase() === "readme.md") return path.join(OUT_DIR, "index.mdx");

  // Convert docs/foo/README.md -> docs-site/foo/index.mdx
  const parts = rel.split("/");
  if (parts.length >= 2 && parts[parts.length - 1].toLowerCase() === "readme.md") {
    return path.join(OUT_DIR, ...parts.slice(0, -1), "index.mdx");
  }

  // Default: .md -> .mdx
  return path.join(OUT_DIR, rel.replace(/\.md$/i, ".mdx"));
}

function writeFile(abs, contents) {
  ensureDir(path.dirname(abs));
  fs.writeFileSync(abs, contents, "utf8");
}

function main() {
  if (!fs.existsSync(SRC_DIR)) {
    console.error(`Missing ${SRC_DIR}`);
    process.exit(1);
  }

  emptyDir(OUT_DIR);

  const mdFiles = walk(SRC_DIR).filter((p) => p.toLowerCase().endsWith(".md"));

  for (const abs of mdFiles) {
    const rel = relToDocs(abs);
    const outAbs = toOutPath(rel);
    const src = readUtf8(abs);
    const out = hasFrontmatter(src) ? src : buildFrontmatter(src) + src;
    writeFile(outAbs, out);
  }

  console.log(`[mintlify-sync] synced ${mdFiles.length} markdown files into ${OUT_DIR}`);
}

main();
