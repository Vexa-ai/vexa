#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Generate src/lib/release-version.generated.json from authoritative sources.
 *
 * Runs in npm `predev` / `prebuild` hooks. Output never committed; a fresh
 * checkout always regenerates from the current tree state.
 *
 * Source of truth, in priority order:
 *   1. NEXT_PUBLIC_VEXA_OSS_VERSION env var (CI / Docker build-arg override),
 *      verified against VERSION / Chart.yaml when they are available
 *   2. Root VERSION
 *   3. deploy/helm/charts/vexa/Chart.yaml `appVersion`
 *      — authoritative source for the OSS release this dashboard ships in
 *   4. Latest git tag matching v\d+\.\d+\.\d+
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
const { execFileSync } = require('child_process');

const HERE = __dirname;
const REPO_ROOT = process.env.VEXA_REPO_ROOT
  ? path.resolve(process.env.VEXA_REPO_ROOT)
  : path.resolve(HERE, '..', '..', '..'); // services/dashboard/scripts → repo root
const VERSION_FILE = path.join(REPO_ROOT, 'VERSION');
const CHART_YAML = path.join(REPO_ROOT, 'deploy', 'helm', 'charts', 'vexa', 'Chart.yaml');
const OUT_FILE = path.join(HERE, '..', 'src', 'lib', 'release-version.generated.json');

function normalizeVersion(value) {
  if (!value) return null;
  const text = String(value).trim().replace(/^v/, '');
  return /^\d+(?:\.\d+)+$/.test(text) ? text : null;
}

function readRootVersion() {
  if (!fs.existsSync(VERSION_FILE)) return null;
  return normalizeVersion(fs.readFileSync(VERSION_FILE, 'utf-8'));
}

function readChartAppVersion() {
  if (!fs.existsSync(CHART_YAML)) return null;
  const text = fs.readFileSync(CHART_YAML, 'utf-8');
  // appVersion: "0.10.6.3"  (line in Chart.yaml)
  const m = text.match(/^\s*appVersion\s*:\s*['"]?(\d+(?:\.\d+)+)['"]?\s*$/m);
  return m ? normalizeVersion(m[1]) : null;
}

function latestGitTag() {
  try {
    const out = execFileSync(
      'git',
      ['-C', REPO_ROOT, 'describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*.[0-9]*.[0-9]*'],
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
    const out = execFileSync(
      'git',
      ['-C', REPO_ROOT, 'log', '-1', '--format=%cs', tag],
      { stdio: ['ignore', 'pipe', 'ignore'] }
    ).toString().trim();
    return out || null;
  } catch {
    return null;
  }
}

function main() {
  const envVersion = normalizeVersion(process.env.NEXT_PUBLIC_VEXA_OSS_VERSION);
  const rootVersion = readRootVersion();
  const chartVersion = readChartAppVersion();
  const gitTagVersion = normalizeVersion(latestGitTag());

  if (process.env.NEXT_PUBLIC_VEXA_OSS_VERSION && !envVersion) {
    throw new Error(
      `[release-version] invalid NEXT_PUBLIC_VEXA_OSS_VERSION: ${process.env.NEXT_PUBLIC_VEXA_OSS_VERSION}`
    );
  }

  const canonicalVersions = [
    ['VERSION', rootVersion],
    ['deploy/helm/charts/vexa/Chart.yaml appVersion', chartVersion],
  ].filter(([, value]) => value);

  if (envVersion) {
    for (const [source, value] of canonicalVersions) {
      if (value !== envVersion) {
        throw new Error(
          `[release-version] NEXT_PUBLIC_VEXA_OSS_VERSION=${envVersion} does not match ${source}=${value}`
        );
      }
    }
  }

  if (rootVersion && chartVersion && rootVersion !== chartVersion) {
    throw new Error(
      `[release-version] VERSION=${rootVersion} does not match deploy/helm/charts/vexa/Chart.yaml appVersion=${chartVersion}`
    );
  }

  const version = envVersion || rootVersion || chartVersion || gitTagVersion;

  if (!version) {
    throw new Error(
      '[release-version] cannot derive version: no env var, no VERSION, no ' +
      'Chart.yaml appVersion, no git tag. Set NEXT_PUBLIC_VEXA_OSS_VERSION ' +
      'or commit a version bump.'
    );
  }

  const releaseDate =
    process.env.NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE ||
    tagCommitDate(`v${version}`) ||
    new Date().toISOString().slice(0, 10);

  const source =
    envVersion
      ? 'env'
      : rootVersion
        ? 'VERSION'
        : chartVersion
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
