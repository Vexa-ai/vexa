/**
 * build-bundle.mjs — rebuild the fixture's ws-bundle.js from the dash-ws brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always loads the CURRENT brick
 * code, not a stale snapshot. esbuild bundles e2e/fixtures/ws-entry.ts (which re-exports createWsClient +
 * createFakeWsTransport from ../../modules/dash-ws/src) into a single browser ESM module. The
 * @vexa/dash-contracts imports inside dash-ws are type-only and get erased — the bundle is the brick's
 * real runtime footprint.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "fixtures", "ws-entry.ts")],
    bundle: true,
    format: "esm",
    target: "es2022",
    outfile: join(here, "fixtures", "ws-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
