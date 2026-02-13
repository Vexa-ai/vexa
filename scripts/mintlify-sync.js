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
const SRC_ASSETS_DIR = path.join(SRC_DIR, "assets");
const OUT_ASSETS_DIR = path.join(OUT_DIR, "assets");

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

function copyDir(src, dest) {
  ensureDir(dest);
  for (const ent of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, ent.name);
    const d = path.join(dest, ent.name);
    if (ent.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
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
  // Mintlify renders `title` as the page H1. Our source markdown already has `# ...`,
  // so we strip the leading H1 from the body during sync. We intentionally do not
  // auto-inject `description` to avoid duplicating the first paragraph on every page.
  const lines = ["---", `title: "${title.replace(/"/g, '\\"')}"`, "---", ""];
  return lines.join("\n");
}

function hasFrontmatter(md) {
  return md.startsWith("---\n");
}

function stripLeadingH1(md) {
  // Remove only an H1 at the very top of the file (common pattern in docs/*).
  // Mintlify will render the title from frontmatter as the visible page heading.
  return md.replace(/^#\s+.+\r?\n(\r?\n)*/u, "");
}

function rewriteLinksForMintlify(md) {
  // Mintlify routes are file-path based but without the `.md/.mdx` extension.
  // Our source-of-truth docs live in `docs/` and use `.md` links (GitHub-friendly).
  // Rewrite internal links for the generated bundle so they don't 404.
  return md.replace(/\]\(([^)]+)\)/g, (m, href) => {
    const h = String(href).trim();
    if (!h) return m;
    if (h.startsWith("#")) return m;
    if (h.startsWith("http://") || h.startsWith("https://")) return m;
    if (h.startsWith("mailto:")) return m;

    const [pathPart, hashPart] = h.split("#");
    const p = (pathPart || "").trim();
    const hash = hashPart ? `#${hashPart}` : "";

    // README in Mintlify is routed as the directory index; for our use-cases `/` is fine.
    if (/^(\.?\.?\/)*README\.md$/i.test(p)) return `](/${hash})`;

    // Only rewrite markdown-file links.
    if (!/\.md$/i.test(p)) return m;

    const withoutExt = p.replace(/\.md$/i, "");
    return `](${withoutExt}${hash})`;
  });
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
    const body = stripLeadingH1(src);
    const rewritten = rewriteLinksForMintlify(body);
    const out = hasFrontmatter(src) ? src : buildFrontmatter(src) + rewritten;
    writeFile(outAbs, out);
  }

  // Copy static assets (logos/images) for Mintlify rendering.
  if (fs.existsSync(SRC_ASSETS_DIR)) {
    fs.rmSync(OUT_ASSETS_DIR, { recursive: true, force: true });
    copyDir(SRC_ASSETS_DIR, OUT_ASSETS_DIR);
  }

  console.log(`[mintlify-sync] synced ${mdFiles.length} markdown files into ${OUT_DIR}`);
}

main();
