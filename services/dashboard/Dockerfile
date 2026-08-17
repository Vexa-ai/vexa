# Build stage
FROM node:20-alpine AS builder

WORKDIR /workspace/services/dashboard

# Copy transcript-rendering built package
COPY packages/transcript-rendering/package.json /workspace/packages/transcript-rendering/package.json
COPY packages/transcript-rendering/dist/ /workspace/packages/transcript-rendering/dist/

# Install the exact dependency graph committed with the dashboard. The package
# is copied to the same relative location expected by the manifests.
COPY services/dashboard/package.json services/dashboard/package-lock.json ./
RUN npm ci --ignore-scripts
# Ensure transcript-rendering is physically copied (not symlinked)
RUN rm -rf node_modules/@vexaai/transcript-rendering && \
    cp -r /workspace/packages/transcript-rendering node_modules/@vexaai/transcript-rendering

# Copy source code
COPY services/dashboard/src ./src
COPY services/dashboard/public ./public
COPY services/dashboard/scripts ./scripts
COPY services/dashboard/next.config.ts services/dashboard/tsconfig.json services/dashboard/postcss.config.mjs services/dashboard/eslint.config.mjs ./
COPY services/dashboard/components.json ./
COPY VERSION /repo/VERSION
COPY deploy/helm/charts/vexa/Chart.yaml /repo/deploy/helm/charts/vexa/Chart.yaml

# Version disclosure (NEXT_PUBLIC_VEXA_OSS_VERSION + RELEASE_DATE).
#
# The generator validates any env/build-arg override against the canonical
# VERSION and Chart.yaml copied above, then the assertion checks the compiled
# Next.js bundle. This prevents digest-retagged or stale-build dashboard images
# from silently displaying an older release identity.
ARG NEXT_PUBLIC_VEXA_OSS_VERSION
ARG NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE
ARG VEXA_API_URL
ENV NEXT_PUBLIC_VEXA_OSS_VERSION=$NEXT_PUBLIC_VEXA_OSS_VERSION
ENV NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE=$NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE
ENV VEXA_API_URL=$VEXA_API_URL
ENV VEXA_REPO_ROOT=/repo

# Build application (npm prebuild → generate-release-version.js → next build)
RUN npm run build && npm run assert-release-version

# Production stage
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /workspace/services/dashboard/public ./public
COPY --from=builder --chown=nextjs:nodejs /workspace/services/dashboard/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /workspace/services/dashboard/.next/static ./.next/static
COPY --chown=nextjs:nodejs services/dashboard/docker-entrypoint.sh ./docker-entrypoint.sh

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

ENTRYPOINT ["./docker-entrypoint.sh"]
