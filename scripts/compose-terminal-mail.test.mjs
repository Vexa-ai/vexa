// The terminal's sign-in mail reads the deployment's mail family, VEXA_MAIL_SMTP_*, and every
// deploy surface hands it every key it reads.
// Run: node --test scripts/compose-terminal-mail.test.mjs   (CI: the gates.yml `static` job)
//
// The email sign-in mails a one-time link through `clients/terminal/src/app/api/auth/mailer.ts`. It
// used to read seven unprefixed SMTP_* keys no contract declared, next to the VEXA_MAIL_SMTP_* family
// flows sends through. The family is now declared in `clients/terminal/config.v1.json` and held on
// compose, Helm and Lite by gate:config-contract (the terminal is adopted for that family). This test
// pins what the gate cannot see from the declaration alone: the names are read FROM the mailer, so a
// setting added there must be declared and wired too, and no unprefixed SMTP_* read comes back.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { guardTree } from "./test-tree.mjs";

guardTree();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const MAILER = read("clients/terminal/src/app/api/auth/mailer.ts");
const DECL = JSON.parse(read("clients/terminal/config.v1.json"));
const COMPOSE = read("deploy/compose/docker-compose.yml");
const ENV_EXAMPLE = read("deploy/compose/.env.example");
const HELM = read("deploy/helm/charts/vexa/templates/deployment-terminal.yaml");
const LITE = read("deploy/lite/entrypoint.sh");

/** The names the mailer reads, e.g. VEXA_MAIL_SMTP_HOST. */
const mailerSettings = [...new Set([...MAILER.matchAll(/process\.env\.([A-Z][A-Z0-9_]*SMTP[A-Z0-9_]*)/g)].map((m) => m[1]))];

/** One top-level service block of the compose file, as text (the line-wise read gate:config-contract uses). */
function service(name) {
  const lines = COMPOSE.split("\n");
  const start = lines.findIndex((l) => l.trimEnd() === `  ${name}:`);
  assert.ok(start >= 0, `compose has no ${name} service`);
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^ {2}[A-Za-z0-9_-]+:\s*$/.test(lines[i]) || /^\S/.test(lines[i])) { end = i; break; }
  }
  return lines.slice(start, end).join("\n");
}

test("the mailer reads the whole VEXA_MAIL_SMTP_* family and no unprefixed SMTP_* key", () => {
  for (const k of ["HOST", "PORT", "FROM", "USER", "PASSWORD", "SECURE", "TLS_INSECURE"]) {
    assert.ok(mailerSettings.includes(`VEXA_MAIL_SMTP_${k}`), `mailer.ts no longer reads VEXA_MAIL_SMTP_${k}`);
  }
  assert.deepEqual(mailerSettings.filter((n) => !n.startsWith("VEXA_MAIL_SMTP_")), [],
    "mailer.ts reads an SMTP setting outside the declared family");
});

test("the terminal's config.v1 declares every key the mailer reads, for compose, Helm and Lite", () => {
  const declared = new Map(DECL.keys.map((k) => [k.key, k]));
  for (const name of mailerSettings) {
    assert.ok(declared.has(name), `clients/terminal/config.v1.json does not declare ${name}`);
    assert.deepEqual(declared.get(name).targets, ["compose", "helm", "lite"], `${name} is not on every surface`);
  }
  assert.equal(declared.get("VEXA_MAIL_SMTP_PASSWORD").secret, true);
});

test("compose, .env.example, Helm and Lite each carry every key the mailer reads", () => {
  const terminal = service("terminal");
  for (const name of mailerSettings) {
    assert.match(terminal, new RegExp(`- ${name}=\\$\\{${name}:-\\}`), `compose terminal does not pass ${name}`);
    assert.match(ENV_EXAMPLE, new RegExp(`^${name}=`, "m"), `.env.example does not name ${name}`);
    assert.match(HELM, new RegExp(`- name: ${name}\\b`), `the Helm terminal does not set ${name}`);
    assert.match(LITE, new RegExp(`^export ${name}=`, "m"), `Lite does not export ${name}`);
  }
  assert.doesNotMatch(terminal, /- SMTP_[A-Z_]+=/, "compose still passes an unprefixed SMTP_* key");
});
