// The compose stack hands the terminal every SMTP setting its sign-in mailer reads.
// Run: node --test scripts/compose-terminal-mail.test.mjs   (CI: the gates.yml `static` job)
//
// The email sign-in mails a one-time link through `clients/terminal/src/app/api/auth/mailer.ts`,
// which reads SMTP_* from its own environment. A compose stack that does not pass those through
// leaves the mailer dialling localhost:1025 inside the terminal container, so no link is ever
// delivered. The names are read FROM the mailer, so a setting added there must be wired here too.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const MAILER = read("clients/terminal/src/app/api/auth/mailer.ts");
const COMPOSE = read("deploy/compose/docker-compose.yml");
const ENV_EXAMPLE = read("deploy/compose/.env.example");

/** The names the mailer reads, e.g. SMTP_HOST. */
const mailerSettings = [...new Set([...MAILER.matchAll(/process\.env\.(SMTP_[A-Z_]+)/g)].map((m) => m[1]))];

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

test("the mailer reads the SMTP settings this test checks", () => {
  for (const name of ["SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USER", "SMTP_PASS", "SMTP_SECURE"]) {
    assert.ok(mailerSettings.includes(name), `mailer.ts no longer reads ${name}`);
  }
});

test("compose passes every SMTP setting the mailer reads into the terminal", () => {
  const terminal = service("terminal");
  for (const name of mailerSettings) {
    assert.match(terminal, new RegExp(`- ${name}=\\$\\{${name}:-\\}`),
      `terminal service does not pass ${name} from the stack's .env`);
  }
});

test(".env.example names every SMTP setting the terminal takes", () => {
  for (const name of mailerSettings) {
    assert.match(ENV_EXAMPLE, new RegExp(`^${name}=`, "m"), `.env.example does not name ${name}`);
  }
});
