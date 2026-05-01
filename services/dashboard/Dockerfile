# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy transcript-rendering built package
COPY packages/transcript-rendering/package.json /transcript-rendering/package.json
COPY packages/transcript-rendering/dist/ /transcript-rendering/dist/

# Install dependencies (package.json only — no lockfile, to avoid path mismatch after sed)
COPY services/dashboard/package.json ./
RUN sed -i 's|"@vexaai/transcript-rendering":.*|"@vexaai/transcript-rendering": "file:/transcript-rendering",|' package.json && \
    npm install --ignore-scripts
# Ensure transcript-rendering is physically copied (not symlinked)
RUN rm -rf node_modules/@vexaai/transcript-rendering && \
    cp -r /transcript-rendering node_modules/@vexaai/transcript-rendering

# Copy source code
COPY services/dashboard/src ./src
COPY services/dashboard/public ./public
COPY services/dashboard/scripts ./scripts
COPY services/dashboard/next.config.ts services/dashboard/tsconfig.json services/dashboard/postcss.config.mjs services/dashboard/eslint.config.mjs ./
COPY services/dashboard/components.json ./

# Version disclosure (NEXT_PUBLIC_VEXA_OSS_VERSION + RELEASE_DATE).
#
# Source of truth lives in deploy/helm/charts/vexa/Chart.yaml appVersion
# (relative to repo root) but Docker's build context is .. — Chart.yaml
# isn't accessible from inside the build's WORKDIR. Caller MUST pass these
# as build-args, extracting from the chart on the host. The generator script
# (scripts/generate-release-version.js, run via npm prebuild hook) consumes
# them as its first-priority source.
ARG NEXT_PUBLIC_VEXA_OSS_VERSION
ARG NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE
ENV NEXT_PUBLIC_VEXA_OSS_VERSION=$NEXT_PUBLIC_VEXA_OSS_VERSION
ENV NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE=$NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE

# Build application (npm prebuild → generate-release-version.js → next build)
RUN npm run build

# Production stage
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --chown=nextjs:nodejs services/dashboard/docker-entrypoint.sh ./docker-entrypoint.sh

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

ENTRYPOINT ["./docker-entrypoint.sh"]
