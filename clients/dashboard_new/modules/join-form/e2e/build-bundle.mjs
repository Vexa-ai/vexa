/**
 * build-bundle.mjs — bundle the fixture (which imports the REAL JoinForm brick source) for the browser.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always loads the CURRENT brick
 * code, not a stale snapshot. esbuild bundles e2e/fixtures/form-entry.tsx — which imports `JoinForm`
 * from ../../src/index.ts and mounts it with react-dom — into a single browser ESM module. React +
 * react-dom are bundled in; the @vexa/dash-contracts imports are type-only and get erased. The bundle
 * is the brick's real runtime footprint mounted in a real DOM.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "fixtures", "form-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    outfile: join(here, "fixtures", "form-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
