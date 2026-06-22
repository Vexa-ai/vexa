/**
 * build-bundle.mjs — rebuild the fixture's vnc-bundle.js from the brick SOURCE.
 *
 * Run as Playwright's globalSetup (and via `npm run bundle`) so the page always loads the CURRENT
 * component, not a stale dist snapshot. esbuild bundles e2e/vnc-entry.tsx (which imports the real
 * <VncView> from ../src/index.ts plus react + react-dom) into a single browser ESM module. The fixture
 * page then calls `window.mountVncView(root, props)`.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export default async function globalSetup() {
  await build({
    entryPoints: [join(here, "vnc-entry.tsx")],
    bundle: true,
    format: "esm",
    target: "es2022",
    jsx: "automatic",
    // React's UMD/CJS bundles read process.env.NODE_ENV; define it so the browser bundle is valid.
    define: { "process.env.NODE_ENV": '"production"' },
    outfile: join(here, "vnc-bundle.js"),
    logLevel: "info",
  });
}

// allow `node build-bundle.mjs` directly too
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await globalSetup();
}
