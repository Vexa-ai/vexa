// npm-locks — the npm projects that install from their own package-lock.json, outside the pnpm tree.
//
// The terminal is one: its image and Lite's terminal stage run `npm ci` from
// clients/terminal/package-lock.json, whose overrides differ from pnpm's, so `pnpm licenses list`
// does not describe what those images install. gate:licenses classifies these locks with the same
// rules as the pnpm tree, and scripts/sbom.mjs inventories them. A lock under a directory that
// carries `.gateignore` is a vendored subtree opted out of the per-directory gates (the retiring
// dashboard), and is skipped here for the same reason.
//
// npm lockfile v2/v3 records each package's declared licence in `packages[path].license`; a package
// that declares none is reported as "Unknown", as pnpm reports it.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, sep } from "node:path";

/** Tracked package-lock.json files, repo-relative, minus those under a `.gateignore` directory. */
export function npmLockfiles(root) {
  let tracked;
  try {
    tracked = execFileSync("git", ["ls-files", "-z", "--", "package-lock.json", "*/package-lock.json"],
      { cwd: root, encoding: "utf8" }).split("\0").filter(Boolean);
  } catch {
    return [];
  }
  return tracked.filter((rel) => {
    for (let d = dirname(rel); d && d !== "."; d = dirname(d)) {
      if (existsSync(join(root, d, ".gateignore"))) return false;
    }
    return existsSync(join(root, rel));
  }).sort();
}

/** One lockfile's installed packages: [{ name, version, license, dev }]. Links and the root are skipped. */
export function npmLockPackages(file) {
  const lock = JSON.parse(readFileSync(file, "utf8"));
  if (!lock.packages) throw new Error(`${file}: lockfileVersion ${lock.lockfileVersion} has no "packages" map (npm 7+ writes one)`);
  const out = [];
  for (const [path, p] of Object.entries(lock.packages)) {
    if (!path || p.link) continue;
    const name = p.name || path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length);
    const lic = typeof p.license === "string" ? p.license : (p.license && p.license.type) || "Unknown";
    out.push({ name, version: p.version || "NOASSERTION", license: lic, dev: p.dev === true });
  }
  return out;
}

/** Every package across the gated npm locks, deduplicated by name@version. */
export function npmLockInventory(root) {
  const locks = npmLockfiles(root);
  const seen = new Map();
  for (const rel of locks) {
    for (const p of npmLockPackages(join(root, rel))) {
      const key = `${p.name}@${p.version}`;
      if (!seen.has(key)) seen.set(key, { ...p, locks: [rel] });
      else seen.get(key).locks.push(rel);
    }
  }
  return { locks: locks.map((l) => l.split(sep).join("/")), packages: [...seen.values()] };
}
