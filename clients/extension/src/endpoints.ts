/**
 * Endpoint resolution — the "one build serves all deployments" piece.
 *
 * ONE extension build runs against every Vexa deployment (desktop, lite, compose,
 * helm). Rather than ship a build per target, the user picks a `deployment` preset
 * (or pastes explicit URLs) and this module resolves the concrete ingest +
 * gateway endpoints. Pure + dependency-free so it unit-tests without a browser
 * (see endpoints.test.ts).
 *
 * Resolution order (per field, independent):
 *   1. an explicit, non-empty URL from settings always wins (any deployment), then
 *   2. the chosen deployment preset's default.
 *
 *   - ingest  = the capture.v1 WebSocket (audio/events/recording).
 *   - gateway = the REST API base (bots list, meeting status, finalize on stop).
 *
 * Presets:
 *   - desktop : ws://localhost:9099/ingest  +  http://localhost:8056
 *               (the all-Node desktop pipeline — the default).
 *   - cloud   : ws://localhost:8092/ingest  +  http://localhost:8056
 *               (compose/helm/lite reached through a local port-forward/tunnel).
 */

export type Deployment = 'desktop' | 'cloud';

export const DEFAULT_DEPLOYMENT: Deployment = 'desktop';

export interface DeploymentPreset {
  ingestUrl: string;
  gatewayUrl: string;
}

/** Built-in presets, keyed by deployment. */
export const DEPLOYMENT_PRESETS: Record<Deployment, DeploymentPreset> = {
  desktop: { ingestUrl: 'ws://localhost:9099/ingest', gatewayUrl: 'http://localhost:8056' },
  cloud:   { ingestUrl: 'ws://localhost:8092/ingest', gatewayUrl: 'http://localhost:8056' },
};

/** Settings shape this resolver reads (a subset of the stored config). */
export interface EndpointConfig {
  /** Chosen preset; falls back to the default when missing/unknown. */
  deployment?: string;
  /** Explicit override for the ingest WebSocket (wins over the preset when non-empty). */
  ingestUrl?: string;
  /** Explicit override for the gateway REST base (wins over the preset when non-empty). */
  gatewayUrl?: string;
}

export interface ResolvedEndpoints {
  deployment: Deployment;
  ingestUrl: string;
  gatewayUrl: string;
}

/** Normalize an arbitrary stored value to a known deployment (default otherwise). */
export function normalizeDeployment(value: unknown): Deployment {
  return value === 'cloud' || value === 'desktop' ? value : DEFAULT_DEPLOYMENT;
}

/**
 * Resolve the concrete ingest + gateway endpoints for a stored config.
 * Explicit non-empty URLs override the chosen preset; otherwise the preset wins.
 */
export function resolveEndpoints(cfg: EndpointConfig = {}): ResolvedEndpoints {
  const deployment = normalizeDeployment(cfg.deployment);
  const preset = DEPLOYMENT_PRESETS[deployment];
  const explicit = (v: string | undefined): string => (v && v.trim() ? v.trim() : '');
  return {
    deployment,
    ingestUrl: explicit(cfg.ingestUrl) || preset.ingestUrl,
    gatewayUrl: explicit(cfg.gatewayUrl) || preset.gatewayUrl,
  };
}
