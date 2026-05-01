/**
 * Single source of truth for "what version of Vexa OSS is this dashboard
 * build pairs with". Surfaced via <VersionChip /> in the header + sidebar.
 *
 * NEVER hardcoded. Values are derived at build time by
 * scripts/generate-release-version.js (runs in npm `prebuild` / `predev`
 * hooks) and written to release-version.generated.json. Sources, in
 * priority order:
 *
 *   1. NEXT_PUBLIC_VEXA_OSS_VERSION / *_RELEASE_DATE env vars
 *      (CI / Docker build-arg override path)
 *   2. deploy/helm/charts/vexa/Chart.yaml `appVersion`
 *      — authoritative source for the OSS release this dashboard ships in
 *   3. Latest git tag matching `v\d+\.\d+\.\d+`
 *
 * If none resolve, the generator throws — build fails loud rather than
 * shipping `unknown`. To bump the version, bump Chart.yaml appVersion (or
 * `git tag vX.Y.Z`) and the next build picks it up automatically.
 */

import generated from "./release-version.generated.json";

export const RELEASE = {
  version: generated.version,
  releaseDate: generated.releaseDate,
  generatedAt: generated.generatedAt,
  source: generated.source,
};

/** GitHub release URL for the current version. */
export function releaseUrl(version: string = RELEASE.version): string {
  return `https://github.com/Vexa-ai/vexa/releases/tag/${version}`;
}
