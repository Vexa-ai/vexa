/**
 * build-bundle.mjs — rebuild the fixture's list-bundle.js from the MeetingsList brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run test:bundle`) so the page always loads the CURRENT
 * brick code, not a stale snapshot. esbuild bundles e2e/fixtures/list-entry.tsx (which imports
 * MeetingsList from ../../src/index.ts + React) into a single browser ESM module. The
 * @vexa/dash-contracts imports inside MeetingsList are type-only and get erased; react + react-dom are
 * bundled in (the component's real runtime footprint), resolved from this brick's node_modules.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "fixtures", "list-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    outfile: join(here, "fixtures", "list-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
