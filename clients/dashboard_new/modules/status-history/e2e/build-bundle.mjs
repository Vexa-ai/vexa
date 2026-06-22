/**
 * build-bundle.mjs — rebuild the fixture's render-bundle.js from the StatusHistory brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run test:bundle`) so the page always loads the CURRENT
 * component code, not a stale snapshot. esbuild bundles fixtures/render-entry.tsx (which mounts
 * StatusHistory from ../../src into #root) into one browser ESM module. The @vexa/dash-contracts import
 * inside StatusHistory is type-only and gets erased — the bundle is the component's real runtime
 * footprint (component + react + react-dom).
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
    loader: { ".ts": "ts", ".tsx": "tsx" },
    outfile: join(here, "fixtures", "render-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
