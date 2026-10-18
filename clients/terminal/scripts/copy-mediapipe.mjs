// SELF-HOSTED BACKGROUND BLUR ASSETS (founder 2026-10-15: no CDN fetch). The call's background blur
// (@livekit/track-processors over @mediapipe/tasks-vision) loads a WebAssembly runtime and one
// segmentation model. Both are served from this app at /mediapipe/ instead of jsdelivr / Google
// storage: the wasm is copied from the installed @mediapipe/tasks-vision (the exact version the
// processor depends on), the model from vendor/mediapipe/. Run before `next build`, the dev server
// and the UI lab; the output (public/mediapipe/) is generated, never committed.
import { createRequire } from "node:module";
import { cpSync, existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const out = join(root, "public", "mediapipe");
const req = createRequire(join(root, "package.json"));
// the package roots, found from their entry points (neither exports its package.json)
const up = (file, name) => { let d = dirname(file); while (!existsSync(join(d, name))) { const p = dirname(d); if (p === d) throw new Error(`no ${name} above ${file}`); d = p; } return d; };
const processor = up(req.resolve("@livekit/track-processors"), "package.json");
const vision = up(createRequire(join(processor, "package.json")).resolve("@mediapipe/tasks-vision"), "wasm");

mkdirSync(join(out, "wasm"), { recursive: true });
for (const f of readdirSync(join(vision, "wasm"))) cpSync(join(vision, "wasm", f), join(out, "wasm", f));
const vendor = join(root, "vendor", "mediapipe");
let models = 0;
// the model and its licence (Apache-2.0, the notice travels with the bytes)
if (existsSync(vendor)) for (const f of readdirSync(vendor)) {
  if (f.endsWith(".tflite")) { cpSync(join(vendor, f), join(out, f)); models++; }
  else if (f.endsWith(".LICENSE.txt")) cpSync(join(vendor, f), join(out, f));
}
const note = "# public/mediapipe (generated)\n\nThe background-blur runtime, copied by `scripts/copy-mediapipe.mjs` from the installed `@mediapipe/tasks-vision` (and the model from `vendor/mediapipe/`). Not committed.\n";
writeFileSync(join(out, "README.md"), note);
writeFileSync(join(out, "wasm", "README.md"), note);
console.log(`[mediapipe] wasm from ${vision} → public/mediapipe/wasm; ${models} model(s) from vendor/mediapipe`);
// A build without the model shipped a blur that could never start (2026-10-16), so it is an error now.
if (!models) { console.error("[mediapipe] no segmentation model in vendor/mediapipe/: background blur cannot start without it"); process.exit(1); }
