#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Fail the dashboard build if the generated release identity did not make it
 * into the compiled Next.js bundle.
 */

const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const APP_ROOT = path.resolve(HERE, '..');
const REPO_ROOT = process.env.VEXA_REPO_ROOT
  ? path.resolve(process.env.VEXA_REPO_ROOT)
  : path.resolve(APP_ROOT, '..', '..');
const GENERATED = path.join(APP_ROOT, 'src', 'lib', 'release-version.generated.json');
const STATIC_DIR = path.join(APP_ROOT, '.next', 'static');

function normalizeVersion(value) {
  if (!value) return null;
  const text = String(value).trim().replace(/^v/, '');
  return /^\d+(?:\.\d+)+$/.test(text) ? text : null;
}

function readVersionFile() {
  const file = path.join(REPO_ROOT, 'VERSION');
  if (!fs.existsSync(file)) return null;
  return normalizeVersion(fs.readFileSync(file, 'utf-8'));
}

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = `${dir}${path.sep}${entry.name}`;
    return entry.isDirectory() ? walk(fullPath) : [fullPath];
  });
}

function main() {
  const generated = JSON.parse(fs.readFileSync(GENERATED, 'utf-8'));
  const expected =
    normalizeVersion(process.env.EXPECTED_VEXA_OSS_VERSION) ||
    normalizeVersion(process.env.NEXT_PUBLIC_VEXA_OSS_VERSION) ||
    readVersionFile();

  if (!expected) {
    throw new Error('[release-version] cannot assert bundle version: no expected version');
  }

  const actual = normalizeVersion(generated.version);
  if (actual !== expected) {
    throw new Error(`[release-version] generated version ${generated.version} does not match expected ${expected}`);
  }

  const bundleFiles = walk(STATIC_DIR).filter((file) => /\.(js|json)$/.test(file));
  if (bundleFiles.length === 0) {
    throw new Error(`[release-version] no compiled bundle files found under ${STATIC_DIR}`);
  }

  const expectedNeedle = `"${expected}"`;
  const hit = bundleFiles.some((file) => fs.readFileSync(file, 'utf-8').includes(expectedNeedle));
  if (!hit) {
    throw new Error(`[release-version] compiled dashboard bundle does not contain ${expectedNeedle}`);
  }

  console.log(`[release-version] compiled bundle contains ${expected}`);
}

main();
