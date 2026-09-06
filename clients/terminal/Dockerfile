# syntax=docker/dockerfile:1
# Vexa Terminal (v0.12) — Next.js 15 app with a CUSTOM server (server.mjs) that proxies the `/ws`
# WebSocket upgrade to the gateway server-side. Because the server is custom (not Next's standalone
# server.js), we ship the pruned production node_modules rather than `output: standalone` — server.mjs
# imports `next`, `@next/env`, and `ws`, which a standalone trace would not all include.
# Build context: clients/terminal (self-contained npm project; no @vexa/* workspace deps).

# RUNTIME_DEPS selects the node_modules the runtime stage ships (see the deps-* stages below):
#   prod (default) — pruned production deps; the lean image every release build uses.
#   dev            — the FULL npm ci tree from the build stage. Required for hot/dev-mode runs
#                    (compose hot overlay sets NODE_ENV=development): dev-mode Next needs
#                    @types/node, @types/react and the tailwind/postcss toolchain at runtime,
#                    and when they are missing it npm-installs them AT BOOT over the network —
#                    a ~40s+ start that now hard-fails on a peer conflict (Next pins
#                    @types/node@20.17.6; vite@8's peer range rejects it). Shipping the deps
#                    makes dev boot instant and offline-safe.
ARG RUNTIME_DEPS=prod

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
# Bot display name baked into the client bundle (src/surfaces/defaultBotName.ts). Empty (default) →
# the terminal omits bot_name on join and meeting-api's DEFAULT_BOT_NAME decides at RUNTIME, which is
# the knob operators should use. Set this only to pin a name into the image; it then wins over the
# deployment's DEFAULT_BOT_NAME and a rename costs a rebuild.
ARG NEXT_PUBLIC_DEFAULT_BOT_NAME=""
ENV NEXT_PUBLIC_DEFAULT_BOT_NAME=$NEXT_PUBLIC_DEFAULT_BOT_NAME
# The BUILD STAMP the reload bar compares against (PRD decision 39). Inlined into the client bundle
# like every other NEXT_PUBLIC_*, which is the property that makes it work: a tab running an old
# bundle carries the old string, the server route serving the new one reports the new string, and
# that difference IS "a new version is ready". Empty → "unknown", and a deployment of unstamped
# images simply never offers a reload (no false positives, no silent wrong ones).
ARG NEXT_PUBLIC_BUILD_ID=""
ENV NEXT_PUBLIC_BUILD_ID=$NEXT_PUBLIC_BUILD_ID
# Regular build (not standalone): produces .next served by the custom server in production.
#
# `.next/cache` is deleted in the SAME layer that creates it. It is webpack's incremental-compile
# cache (`cache/webpack` 522 MB + `cache/swc` 12 kB on 2026-09-06) and it is not part of the built
# app: the runtime serves `.next/server`, `.next/static` and the manifests, and nothing in this image
# ever runs `next build` again. Shipped, it was 522 MB of a 1.34 GB image — roughly half of every
# transfer, the air-gapped registries a delivery is pushed into included. Same layer rather than a
# later `RUN rm`, so the builder never materialises it either.
RUN npm run build && rm -rf .next/cache

# deps-prod: pruned production deps. typescript must ALSO be present — Next loads the TypeScript
# next.config.ts at runtime and npm-installs its own at BOOT when it's absent. `npm i typescript`
# here CANNOT provide it: NODE_ENV=production makes npm silently omit anything matching a
# devDependency. So it is COPIED from the build stage — the exact compiler `next build` ran with.
FROM node:22-slim AS deps-prod
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/node_modules/typescript ./node_modules/typescript

# deps-dev: the build stage already holds the full npm ci tree.
FROM build AS deps-dev

# ARG-selected alias (COPY --from does not expand variables; FROM does).
FROM deps-${RUNTIME_DEPS} AS deps

FROM node:22-slim AS runtime
# The server-side proxy gate reads the mode at REQUEST time (process.env), so the runtime stage must
# carry the same value the bundle was built with — client gating and edge gating stay in lockstep.
ARG NEXT_PUBLIC_TERMINAL_MODE=""
ARG NEXT_PUBLIC_BUILD_ID=""
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    NEXT_PUBLIC_TERMINAL_MODE=$NEXT_PUBLIC_TERMINAL_MODE \
    NEXT_PUBLIC_BUILD_ID=$NEXT_PUBLIC_BUILD_ID
# F67 — THE VARIANT, AS A FACT ON THE IMAGE. The minutes terminal and the default terminal come out
# of this one Dockerfile and differ only by the build arg above. Both build, both pass the suite,
# both start, and the tag is whatever the builder typed — so on 2026-09-02 a `docker compose build`
# produced the DEFAULT variant under a `-minutes` name, twice, and it was caught only by grepping
# three UI strings out of the client chunks. `Desk` and `wsLinks` were present in both, so a smaller
# check would have passed.
#
# A LABEL rather than only the ENV: it survives `docker save`/`load` and a registry round-trip, it is
# readable without starting the container, and it cannot be shadowed at `docker run -e` time the way
# an env var can. "default" is spelled out because an empty string reads as "nobody set this", and
# the swap guard has to tell the default variant from a broken read.
LABEL ai.vexa.terminal.mode="${NEXT_PUBLIC_TERMINAL_MODE:-default}"
# THE PAIRING NUMBER, AS A FACT ON THE IMAGE (F55/F77, PRD decision 39). The terminal and the
# server have to move together: on 2026-09-02 a terminal built ahead of agent-api shipped a button
# whose request every running server rejected, and a terminal swapped onto an old server pointed a
# tab at a note path that server never writes. Both were caught by a human reading a diff.
#
# This label is what lets the swap catch them instead. It is the agent-api contract number the
# BUNDLE was built against (`TERMINAL_AGENT_API` in src/version.ts — a test pins the two together),
# and `deploy.sh` refuses a terminal whose label is not the `api` the agent-api that is actually
# serving answers on GET /api/version. A literal rather than a build arg on purpose: a build arg
# can be forgotten, and the mode label directly above is here because exactly that happened.
LABEL ai.vexa.terminal.agent_api="1"
LABEL ai.vexa.terminal.build="${NEXT_PUBLIC_BUILD_ID:-unknown}"
WORKDIR /app
COPY package.json package-lock.json ./
# node_modules from the RUNTIME_DEPS-selected stage (prod = pruned + typescript; dev = full tree).
COPY --from=deps /app/node_modules ./node_modules
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
