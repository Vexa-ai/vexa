/**
 * build-bundle.mjs — rebuild the fixture's mount-bundle.js from the brick SOURCE.
 *
 * Runs as Playwright's globalSetup (and via `npm run test:bundle`) so the page always mounts the CURRENT
 * component, not a stale snapshot. esbuild bundles e2e/fixtures/mount-entry.tsx (which mounts the real
 * WsEventLog from ../../src plus the golden events) into a single browser ESM module, bundling
 * react/react-dom in. The @vexa/dash-contracts import inside the brick is type-only and gets erased.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

const common = {
  bundle: true,
  format: "esm",
  target: "es2022",
  jsx: "automatic",
  logLevel: "info",
};

export default async function globalSetup() {
  await Promise.all([
    build({
      ...common,
      entryPoints: [join(here, "fixtures", "mount-entry.tsx")],
      outfile: join(here, "fixtures", "mount-bundle.js"),
    }),
    build({
      ...common,
      entryPoints: [join(here, "fixtures", "mount-empty-entry.tsx")],
      outfile: join(here, "fixtures", "mount-empty-bundle.js"),
    }),
  ]);
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
