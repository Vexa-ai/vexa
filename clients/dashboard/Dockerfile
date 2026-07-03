# syntax=docker/dockerfile:1
# 0.12 dashboard image — the vendored Next.js app (clients/dashboard) wired to the compose gateway.
# Build context = REPO ROOT (so it can pull in the vendored packages/transcript-rendering):
#   docker build -f clients/dashboard/Dockerfile --build-arg VEXA_API_URL=http://gateway:8000 -t vexa-dashboard:dev .
# (0.12 layout: clients/dashboard + packages/ at root — main's image used services/dashboard.)
FROM node:20-alpine AS builder
WORKDIR /app

# The vendored transcript-rendering dep (prebuilt dist), rewired from the file: path to an absolute one.
COPY packages/transcript-rendering/package.json /transcript-rendering/package.json
COPY packages/transcript-rendering/dist/ /transcript-rendering/dist/
COPY clients/dashboard/package.json ./
RUN sed -i 's|"@vexaai/transcript-rendering": "file:../../packages/transcript-rendering"|"@vexaai/transcript-rendering": "file:/transcript-rendering"|' package.json \
 && npm install --ignore-scripts --no-audit --no-fund \
 && rm -rf node_modules/@vexaai/transcript-rendering && cp -r /transcript-rendering node_modules/@vexaai/transcript-rendering

COPY clients/dashboard/src ./src
COPY clients/dashboard/public ./public
COPY clients/dashboard/scripts ./scripts
COPY clients/dashboard/next.config.ts clients/dashboard/tsconfig.json clients/dashboard/postcss.config.mjs clients/dashboard/eslint.config.mjs clients/dashboard/components.json ./

# next.config.ts requires VEXA_API_URL at build time (bakes the /ws + /b rewrites); the release-version
# generator takes the explicit env (0.12 has no helm Chart.yaml to read from).
ARG VEXA_API_URL
ENV VEXA_API_URL=$VEXA_API_URL \
    NEXT_PUBLIC_VEXA_OSS_VERSION=0.12.0 \
    NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE=2026-06-21
RUN npm run build

# ── production runner (Next.js standalone) ──
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY clients/dashboard/docker-entrypoint.sh ./docker-entrypoint.sh
EXPOSE 3000
ENTRYPOINT ["./docker-entrypoint.sh"]
