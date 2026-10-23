// @vitest-environment node
/** The terminal's Connections configuration, declared and held to its deploy surfaces.
 *
 * The terminal is not a config.v1-adopted service (gate:config-contract scans Python), so this test
 * is its declaration for the keys the Connections routes read: every `process.env.X` in this
 * directory is in DECLARED, and every declared key is plumbed on the terminal in compose and in the
 * chart. It also holds the role-key isolation on compose: only the terminal and the broker mount the
 * human key; agent-api and every other service never do. (The chart's half is test_template.sh.) */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';

const HERE = join(__dirname, '..');
const ROOT = join(HERE, '../../../../../..');
const DECLARED: Record<string, string> = {
  VEXA_CONNECTIONS_BROKER_URL: 'the credential broker base URL; unset, the panel answers "unavailable"',
  VEXA_CONNECTIONS_HUMAN_KEY_FILE: 'file holding the human role key; mounted into this service only',
  VEXA_CONNECTIONS_PUBLIC_ORIGIN: 'the origin browser POSTs must come from; falls back to NEXTAUTH_URL',
  NEXTAUTH_URL: "the terminal's own public URL (already plumbed for sign-in)",
};

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? (e.name === '__tests__' ? [] : sources(join(dir, e.name))) : e.name.endsWith('.ts') ? [join(dir, e.name)] : []);
}

function composeService(name: string): string {
  const lines = readFileSync(join(ROOT, 'deploy/compose/docker-compose.yml'), 'utf8').split('\n');
  const start = lines.findIndex((l) => l.trimEnd() === `  ${name}:`);
  expect(start, `compose service ${name}`).toBeGreaterThan(-1);
  const end = lines.findIndex((l, i) => i > start && /^ {2}[A-Za-z0-9_-]+:\s*$/.test(l.trimEnd()));
  return lines.slice(start, end < 0 ? undefined : end).join('\n');
}

test('every key the Connections routes read is declared', () => {
  const read = new Set(sources(HERE).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/process\.env\.([A-Z][A-Z0-9_]+)/g)].map((m) => m[1])));
  expect([...read].sort()).toEqual(Object.keys(DECLARED).sort());
});

test('every declared key is plumbed on the terminal in compose and in the chart', () => {
  const compose = composeService('terminal');
  const helm = readFileSync(join(ROOT, 'deploy/helm/charts/vexa/templates/deployment-terminal.yaml'), 'utf8');
  for (const key of Object.keys(DECLARED)) {
    expect(compose, `compose terminal sets ${key}`).toMatch(new RegExp(`^\\s+- ${key}=`, 'm'));
    expect(helm, `helm terminal sets ${key}`).toMatch(new RegExp(`- name: ${key}\\b`));
  }
});

test('on compose, only the key generator, the broker and the terminal mount the human key', () => {
  const text = readFileSync(join(ROOT, 'deploy/compose/docker-compose.yml'), 'utf8');
  const section = text.slice(text.indexOf('\nservices:\n'), text.indexOf('\nvolumes:\n'));
  const services = [...section.matchAll(/^ {2}([a-z][a-z0-9-]*):\s*$/gm)].map((m) => m[1]);
  const mounting = services.filter((s) => composeService(s).includes('connections-human-key:'));
  expect(mounting.sort()).toEqual(['connections-keys', 'credential-broker', 'terminal']);
  expect(composeService('agent-api')).not.toContain('connections-human-key');
  expect(composeService('agent-api')).not.toContain('connections-store-key');
});
