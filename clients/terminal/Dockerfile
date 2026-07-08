# syntax=docker/dockerfile:1
# Vexa Terminal (v0.12) — Next.js 15 app with a CUSTOM server (server.mjs) that proxies the `/ws`
# WebSocket upgrade to the gateway server-side. Because the server is custom (not Next's standalone
# server.js), we ship the pruned production node_modules rather than `output: standalone` — server.mjs
# imports `next`, `@next/env`, and `ws`, which a standalone trace would not all include.
# Build context: clients/terminal (self-contained npm project; no @vexa/* workspace deps).

# node:22 — tinykeys@4 requires node >=22; 20 only warns but we pin 22 to match engines.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Google Analytics measurement id (G-XXXXXXXXXX). NEXT_PUBLIC_* is inlined into the client bundle at
# BUILD time, so it must be present BEFORE `next build` (pass via --build-arg / compose build.args).
# Empty → GA renders nothing (opt-in). Changing it requires a rebuild.
ARG NEXT_PUBLIC_GA_MEASUREMENT_ID=""
ENV NEXT_PUBLIC_GA_MEASUREMENT_ID=$NEXT_PUBLIC_GA_MEASUREMENT_ID
# Terminal mode (src/app/mode.ts). "meetings" builds a meetings-only terminal: only the meetings
# list + meeting/canvas tabs + the API-tokens surface register, and the server proxy refuses
# agent-api paths (404). Empty (default) keeps every surface. NEXT_PUBLIC_* → set BEFORE `next build`.
ARG NEXT_PUBLIC_TERMINAL_MODE=""
ENV NEXT_PUBLIC_TERMINAL_MODE=$NEXT_PUBLIC_TERMINAL_MODE
# Regular build (not standalone): produces .next served by the custom server in production.
RUN npm run build

FROM node:22-slim AS runtime
# The server-side proxy gate reads the mode at REQUEST time (process.env), so the runtime stage must
# carry the same value the bundle was built with — client gating and edge gating stay in lockstep.
ARG NEXT_PUBLIC_TERMINAL_MODE=""
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    NEXT_PUBLIC_TERMINAL_MODE=$NEXT_PUBLIC_TERMINAL_MODE
WORKDIR /app
# Prod-only deps (keeps next/react/ws; drops the build toolchain). typescript must ALSO be present —
# Next loads the TypeScript next.config.ts at runtime and npm-installs its own at BOOT when it's
# absent (a ~40s network-dependent start; breaks air-gapped pods). `npm i typescript` here CANNOT
# provide it: NODE_ENV=production makes npm silently omit anything matching a devDependency. So it
# is COPIED from the build stage below — the exact compiler `next build` ran with.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
# The built app + the custom server + runtime config it reads.
COPY --from=build /app/node_modules/typescript ./node_modules/typescript
COPY --from=build /app/.next ./.next
COPY public ./public
COPY server.mjs next.config.ts tsconfig.json ./
# next-env.d.ts is gitignored (Next.js generates it during `next build`); take it from the build stage
# rather than the build context, where it is absent on a clean clone.
COPY --from=build /app/next-env.d.ts ./
COPY src ./src
EXPOSE 3000
# `npm start` === PORT=3000 NODE_ENV=production node server.mjs
CMD ["node", "server.mjs"]
