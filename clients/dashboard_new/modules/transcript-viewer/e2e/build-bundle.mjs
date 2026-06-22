/**
 * build-bundle.mjs — rebuild the fixture's render-bundle.js from the TranscriptViewer brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always loads the CURRENT brick
 * code, not a stale snapshot. esbuild bundles e2e/fixtures/render-entry.tsx (which mounts
 * TranscriptViewer from ../../src/index.ts with react/react-dom) into a single browser ESM module. The
 * @vexa/dash-contracts imports inside the component are type-only and get erased — the bundle is the
 * brick's real runtime footprint (component + react).
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "fixtures", "render-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    outfile: join(here, "fixtures", "render-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
