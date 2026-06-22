/**
 * build-bundle.mjs — rebuild the fixture's players-bundle.js from the brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always mounts the CURRENT brick
 * components, not a stale snapshot. esbuild bundles e2e/fixtures/players-entry.tsx (which imports
 * AudioPlayer + VideoPlayer from ../../src plus react/react-dom) into one browser ESM module. The
 * @vexa/dash-contracts imports inside the brick are type-only and get erased — the bundle is the brick's
 * real runtime footprint (the components + React).
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "fixtures", "players-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    outfile: join(here, "fixtures", "players-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
