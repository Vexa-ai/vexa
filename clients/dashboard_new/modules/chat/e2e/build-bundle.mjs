/**
 * build-bundle.mjs — rebuild the fixture's chat-bundle.js from the dash-chat brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always mounts the CURRENT brick
 * component, not a stale snapshot. esbuild bundles e2e/chat-entry.tsx (which imports the real ChatPanel
 * from ../src/index.ts and mounts it over the goldens) into a single browser ESM module with react +
 * react-dom inlined. The @vexa/dash-contracts import inside the component is type-only and gets erased —
 * the bundle is the brick's real runtime footprint.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "chat-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    outfile: join(here, "chat-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
