# Docker Image Audit (2026-03-15)

`vexa-bot:dev` — **4.45GB**

## Size breakdown

| Component | Size | Notes |
|-----------|------|-------|
| Base image (`mcr.microsoft.com/playwright:v1.56.0-jammy`) | 2.1GB | X11/xvfb for headless Chromium |
| Playwright browsers | 1.5GB | Chromium 597M, headless shell 323M, WebKit 274M, Firefox 267M, FFmpeg 5M |
| onnxruntime-node (Silero VAD) | 513MB | Runtime dependency for voice activity detection |
| Zoom SDK (`src/platforms/zoom/native/`) | 312MB | libmeetingsdk.so 194M, qt_libs 101M, libcml.so 16M |
| node_modules (other) | 108MB | Includes devDeps that shouldn't ship |

## Optimization opportunities

### High impact

1. **Remove unused Playwright browsers** — only Chromium needed. Firefox (267M), WebKit (274M), headless shell (323M) are dead weight. Change `npx playwright install --with-deps` to `npx playwright install chromium --with-deps`. **Saves ~860MB**.

2. **Deduplicate msedge install** — Dockerfile installs msedge in both ts-builder (line 66) and runtime (line 109) stages. Remove from ts-builder. **Saves 100-300MB**.

3. **`npm prune --production` before runtime COPY** — Dockerfile copies full `node_modules` including devDeps: TypeScript (23M), esbuild (10M), node-gyp (3.6M), @types (2.5M). **Saves ~40MB**.

### Medium impact

4. **Create `.dockerignore`** — currently missing. Should exclude `.git`, `tests/`, `docs/`, `*.md`, `node_modules/`.

5. **Remove `.js.map` from dist** — 48 source map files shipped in production.

### Lower priority

6. **Zoom SDK as separate layer or volume mount** — 312MB baked into source tree. Could fetch at build time or mount at runtime. Harder change.

7. **Lighter base image** — replace Playwright base with `node:20-bullseye` + minimal deps. Risky — X11/xvfb needed for browser automation.

## Estimated savings

Quick wins (items 1-3): **~1GB** → image drops to ~3.4GB

All optimizations: **~1.4-1.8GB** → image drops to ~2.6-3GB

## Current state

Left as-is for now. These optimizations are tracked but not blocking.
