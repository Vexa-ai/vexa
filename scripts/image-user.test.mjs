// Every image this tree builds runs as a non-root USER, or says here why it cannot — and the Helm
// pods that run those images ask for the same user.
// Run: node --test scripts/image-user.test.mjs   (CI: the gates.yml `static` job)
//
// The chart's values used to say "images run non-root via their Dockerfile USER" while no service
// Dockerfile had one. A comment cannot hold that true; this test does. A Dockerfile's FINAL stage
// (what the image runs) must set `USER` to something other than root, or the file must be in
// ROOT_BY_DESIGN with the reason. An entry for a file that does set a non-root USER, or that no
// longer exists, fails too, so the list only ever describes the tree.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { guardTree } from "./test-tree.mjs";

guardTree();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Images that run as root, each with the reason it must. */
const ROOT_BY_DESIGN = {
  "core/runtime/Dockerfile":
    "holds the container engine (docker.sock) and, in the process backend, starts each workload as its own uid",
  "core/agent/services/agent-api/Dockerfile":
    "moves and re-homes workspace trees that root-run workers and per-subject uids write in the shared store",
  "core/agent/worker/Dockerfile":
    "starts as root to stage the turn, then runs every tool as the tools uid (the root-plus-tools-uid design)",
  "core/meetings/services/transcription/Dockerfile":
    "self-built GPU image whose model cache volume existing installs hold as root",
  "core/meetings/services/transcription/Dockerfile.cpu":
    "self-built image whose model cache volume existing installs hold as root",
  "deploy/lite/Dockerfile.lite":
    "one container for the whole stack: supervisord runs as root and starts every child as a distinct non-root uid",
  "core/meetings/modules/join/Dockerfile.env":
    "the join debug harness's base environment, never shipped",
  "core/meetings/modules/join/Dockerfile.debug":
    "the join debug harness, never shipped",
  "core/meetings/services/bot/Dockerfile.mock":
    "a test double of the bot, never shipped",
};

/** The Helm component each non-root image runs as, by the Deployment/Job name suffix it renders. */
const HELM_COMPONENTS = {
  "core/gateway/services/gateway/Dockerfile": ["gateway"],
  "core/identity/services/admin-api/Dockerfile": ["admin-api"],
  "core/meetings/services/meeting-api/Dockerfile": ["meeting-api", "migrations"],
  "core/meetings/services/mcp/Dockerfile": ["mcp"],
  "clients/terminal/Dockerfile": ["terminal"],
  "core/flows/Dockerfile": ["flows-worker", "flows-mailbox", "flows-api"],
  "core/agent/services/credential-broker/Dockerfile": ["credential-broker"],
};

const dockerfiles = execFileSync("git", ["ls-files"], { cwd: ROOT, encoding: "utf8" })
  .split("\n")
  .filter((p) => /(^|\/)Dockerfile(\.[A-Za-z0-9_-]+)?$/.test(p) && !/\.(dockerignore|md)$/.test(p));

/** The USER of a Dockerfile's final stage ("" when it sets none), continuation lines joined. */
function finalUser(rel) {
  const lines = readFileSync(join(ROOT, rel), "utf8").replace(/\\\n/g, " ").split("\n")
    .map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  let user = "";
  for (const line of lines) {
    const [word, ...rest] = line.split(/\s+/);
    if (/^FROM$/i.test(word)) user = "";
    if (/^USER$/i.test(word)) user = rest.join(" ");
  }
  return user;
}
const isRoot = (user) => !user || /^(0|root)(:.*)?$/.test(user);
const uidOf = (user) => Number.parseInt(user.split(":")[0], 10);

test("the inventory is the tree: every Dockerfile is found, and every reasoned entry exists", () => {
  assert.ok(dockerfiles.length >= 15, `only ${dockerfiles.length} Dockerfiles found`);
  for (const rel of Object.keys(ROOT_BY_DESIGN))
    assert.ok(existsSync(join(ROOT, rel)), `${rel} is listed as root-by-design but does not exist`);
});

test("every image runs as a non-root USER, or is root by a stated design", () => {
  for (const rel of dockerfiles) {
    const user = finalUser(rel);
    if (rel in ROOT_BY_DESIGN) {
      assert.ok(isRoot(user), `${rel} sets USER ${user} — drop it from ROOT_BY_DESIGN`);
      assert.ok(ROOT_BY_DESIGN[rel].length > 20, `${rel} needs a reason`);
      continue;
    }
    assert.ok(!isRoot(user), `${rel}: the final stage runs as root — add a non-root USER, or list it in ROOT_BY_DESIGN with the reason`);
  }
});

test("the Helm pods running a non-root image ask for that image's uid", { skip: !hasHelm() && "helm not installed" }, () => {
  const chart = join(ROOT, "deploy/helm/charts/vexa");
  const render = execFileSync("helm", ["template", "vexa", chart, "-n", "vexa", "-f", join(chart, "values-test.yaml"),
    "--set", "flows.enabled=true", "--set", "flows.mail.enabled=true", "--set", "migrations.enabled=true"], { encoding: "utf8" });
  const pods = podContexts(render);
  for (const [rel, components] of Object.entries(HELM_COMPONENTS)) {
    const uid = uidOf(finalUser(rel));
    assert.ok(Number.isInteger(uid) && uid > 0, `${rel} has no numeric non-root USER`);
    for (const component of components) {
      const ctx = pods.get(`vexa-vexa-${component}`);
      assert.ok(ctx, `no rendered pod for ${component}`);
      assert.equal(ctx.runAsNonRoot, "true", `${component}: runAsNonRoot is not set`);
      assert.equal(Number(ctx.runAsUser), uid, `${component}: runAsUser ${ctx.runAsUser} is not ${rel}'s USER ${uid}`);
    }
  }
});

function hasHelm() {
  try { execFileSync("helm", ["version", "--short"], { stdio: "ignore" }); return true; } catch { return false; }
}

/** name → the pod template's securityContext scalars, read from a `helm template` render. */
function podContexts(render) {
  const out = new Map();
  for (const doc of render.split(/^---$/m)) {
    const kind = (doc.match(/^kind: (\S+)/m) || [])[1];
    if (kind !== "Deployment" && kind !== "Job") continue;
    const name = (doc.match(/^metadata:\n(?:  .*\n)*?  name: (\S+)/m) || [])[1];
    const block = (doc.match(/^ {6}securityContext:\n((?: {8}.*\n)+)/m) || [])[1] || "";
    const ctx = Object.fromEntries([...block.matchAll(/^ {8}(\w+): (\S+)$/gm)].map((m) => [m[1], m[2]]));
    if (name) out.set(name, ctx);
  }
  return out;
}
