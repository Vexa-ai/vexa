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
# Regular build (not standalone): produces .next served by the custom server in production.
RUN npm run build

FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0
WORKDIR /app
# Prod-only deps (keeps next/react/ws; drops the build toolchain), PLUS typescript — Next loads the
# TypeScript next.config.ts at runtime and would otherwise try to auto-install it (no network in a pod).
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm i --no-save typescript@5 && npm cache clean --force
# The built app + the custom server + runtime config it reads.
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
