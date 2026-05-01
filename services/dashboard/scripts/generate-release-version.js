#!/usr/bin/env node
/**
 * Generate src/lib/release-version.generated.json from authoritative sources.
 *
 * Runs in npm `predev` / `prebuild` hooks. Output never committed; a fresh
 * checkout always regenerates from the current tree state.
 *
 * Source of truth, in priority order:
 *   1. NEXT_PUBLIC_VEXA_OSS_VERSION env var (CI / Docker build-arg override)
 *   2. deploy/helm/charts/vexa/Chart.yaml `appVersion`
 *      — authoritative source for the OSS release this dashboard ships in
 *   3. Latest git tag matching v\d+\.\d+\.\d+
 *
 * Release date in priority order:
 *   1. NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE env var
 *   2. Commit date of the matching git tag
 *   3. Today's date (last-resort)
 *
 * Throws if no source resolves — build fails loud rather than ships unknown.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const HERE = __dirname;
const REPO_ROOT = path.resolve(HERE, '..', '..', '..'); // services/dashboard/scripts → repo root
const CHART_YAML = path.join(REPO_ROOT, 'deploy', 'helm', 'charts', 'vexa', 'Chart.yaml');
const OUT_FILE = path.join(HERE, '..', 'src', 'lib', 'release-version.generated.json');

function readChartAppVersion() {
  if (!fs.existsSync(CHART_YAML)) return null;
  const text = fs.readFileSync(CHART_YAML, 'utf-8');
  // appVersion: "0.10.5"  (line in Chart.yaml)
  const m = text.match(/^\s*appVersion\s*:\s*['"]?(\d+\.\d+\.\d+)['"]?\s*$/m);
  return m ? `v${m[1]}` : null;
}

function latestGitTag() {
  try {
    const out = execSync(
      'git -C ' + JSON.stringify(REPO_ROOT) + ' describe --tags --abbrev=0 --match "v[0-9]*.[0-9]*.[0-9]*"',
      { stdio: ['ignore', 'pipe', 'ignore'] }
    ).toString().trim();
    return out || null;
  } catch {
    return null;
  }
}

function tagCommitDate(tag) {
  if (!tag) return null;
  try {
    const out = execSync(
      'git -C ' + JSON.stringify(REPO_ROOT) + ` log -1 --format=%cs ${JSON.stringify(tag)}`,
      { stdio: ['ignore', 'pipe', 'ignore'] }
    ).toString().trim();
    return out || null;
  } catch {
    return null;
  }
}

function main() {
  const version =
    process.env.NEXT_PUBLIC_VEXA_OSS_VERSION ||
    readChartAppVersion() ||
    latestGitTag();

  if (!version) {
    throw new Error(
      '[release-version] cannot derive version: no env var, no Chart.yaml ' +
      'appVersion, no git tag. Set NEXT_PUBLIC_VEXA_OSS_VERSION or commit ' +
      'a chart bump.'
    );
  }

  const releaseDate =
    process.env.NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE ||
    tagCommitDate(version) ||
    new Date().toISOString().slice(0, 10);

  const source =
    process.env.NEXT_PUBLIC_VEXA_OSS_VERSION
      ? 'env'
      : readChartAppVersion()
        ? 'deploy/helm/charts/vexa/Chart.yaml'
        : 'git tag';

  const payload = {
    version,
    releaseDate,
    generatedAt: new Date().toISOString(),
    source,
  };

  fs.mkdirSync(path.dirname(OUT_FILE), { recursive: true });
  fs.writeFileSync(OUT_FILE, JSON.stringify(payload, null, 2) + '\n');

  console.log(`[release-version] ${version} · ${releaseDate} (source: ${source})`);
}

main();
