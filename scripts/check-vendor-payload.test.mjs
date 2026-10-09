// Tests OF the vendor-payload absence checker — the instrument gate:vendor-payload trusts (P17, ADR-0039).
//
// The synthetic repos are real git repositories (git init + git add), because "tracked" means what git
// tracks and "ignored" means what git ignores: a fake of either would test the fake. The block and the
// parity fact are copied from THIS repository, so the semantics under test are the shipped ones. The
// last test runs the checker against this repository: the tip is green.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { checkVendorPayload, blockOf, linkedLibraries, isPayload, NATIVE_DIR, EXCLUSION_FILES, PARITY_FACT_ID } from "./check-vendor-payload.mjs";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
const BLOCK = (() => {
  const text = readFileSync(join(REPO, ".gitignore"), "utf8");
  const b = blockOf(text);
  return text.slice(b.index, b.end) + "\n";
})();
const FACT = JSON.parse(readFileSync(join(REPO, "scripts/parity.json"), "utf8")).facts.find((f) => f.id === PARITY_FACT_ID);
const JOIN = "core/meetings/contracts/sdk-join.v1";
const CAPTURE = "core/meetings/contracts/sdk-capture.v1";
const ROW = { name: "Fixture SDK", link: "meetingsdk", license: "LicenseRef-Fixture", contracts: [JOIN, CAPTURE], subprocess: `${dirname(NATIVE_DIR)}/worker.cjs`, reason: "fixture", approved: "fixture" };

function fixture(over = {}) {
  const root = mkdtempSync(join(tmpdir(), "vendor-payload-"));
  const files = {
    ".gitignore": "node_modules/\n" + BLOCK,
    ".dockerignore": "node_modules/\n" + BLOCK,
    "deploy/lite/Dockerfile.lite.dockerignore": "**/node_modules\n" + BLOCK,
    "scripts/parity.json": JSON.stringify({ contract: "parity.v1", facts: [FACT] }),
    "license-exceptions.json": JSON.stringify({ categoryB: [{ package: "Qt5Core", link: "Qt5Core", reason: "fixture" }], operatorSupplied: [ROW] }),
    "contracts.seal.json": JSON.stringify({ [JOIN]: "a", [CAPTURE]: "b" }),
    [`${NATIVE_DIR}/README.md`]: "# Native wrapper\n",
    [`${NATIVE_DIR}/binding.gyp`]: '{"targets":[{"libraries":["-L<(zoom_sdk_dir)","-lmeetingsdk","<!@(pkg-config --libs Qt5Core)"]}]}\n',
    [`${NATIVE_DIR}/zoom_wrapper.cpp`]: "// wrapper\n",
    [`${dirname(NATIVE_DIR)}/worker.cjs`]: "// worker\n",
    "core/meetings/services/bot/src/index.ts": "export const lane = 'browser';\n",
    "core/meetings/services/bot/package.json": JSON.stringify({ name: "bot", scripts: { test: "node --test" } }),
    "deploy/compose/docker-compose.yml": "services:\n  bot:\n    image: vexaai/bot\n",
    "deploy/compose/README.md": "The native-meeting path is not part of Compose.\n",
    ...over,
  };
  for (const [p, body] of Object.entries(files)) {
    if (body === null) continue;
    mkdirSync(join(root, dirname(p)), { recursive: true });
    writeFileSync(join(root, p), body);
  }
  execFileSync("git", ["init", "-q"], { cwd: root, stdio: "pipe" });
  execFileSync("git", ["add", "-A"], { cwd: root, stdio: "pipe" });
  return root;
}
const track = (root, p, body = "payload") => {
  mkdirSync(join(root, dirname(p)), { recursive: true });
  writeFileSync(join(root, p), body);
  execFileSync("git", ["add", "-f", p], { cwd: root, stdio: "pipe" });
};
const errsOf = (root) => checkVendorPayload(root).errs;
const withFixture = (over, fn) => { const root = fixture(over); try { fn(root); } finally { rmSync(root, { recursive: true, force: true }); } };

test("a clean tree passes, and a payload left on disk but untracked is ignored, not an error", () => withFixture({}, (root) => {
  assert.deepEqual(errsOf(root), []);
  mkdirSync(join(root, NATIVE_DIR, "h"), { recursive: true });
  writeFileSync(join(root, NATIVE_DIR, "libmeetingsdk.so"), "operator file");
  writeFileSync(join(root, NATIVE_DIR, "h", "zoom_sdk.h"), "operator file");
  assert.equal(execFileSync("git", ["ls-files", "--others", "--exclude-standard"], { cwd: root }).toString(), "", "git must not even offer the operator's files");
  assert.deepEqual(errsOf(root), []);
}));

test("a TRACKED fake payload fails the gate, wherever it is", () => withFixture({}, (root) => {
  track(root, `${NATIVE_DIR}/libmeetingsdk.so`);
  track(root, "tools/prebuilt/addon.node");
  track(root, "vendor/sdk/qt_libs/Qt/lib/README");
  track(root, "vendor/sdk/h/zoom_sdk.h");
  track(root, "vendor/sdk/libfoo.so.5.15");
  const e = errsOf(root).filter((x) => x.startsWith("tracked:"));
  for (const p of [`${NATIVE_DIR}/libmeetingsdk.so`, "tools/prebuilt/addon.node", "vendor/sdk/qt_libs/Qt/lib/README", "vendor/sdk/h/zoom_sdk.h", "vendor/sdk/libfoo.so.5.15"])
    assert.ok(e.some((x) => x.includes(p)), `${p} must be named: ${e.join(" | ")}`);
}));

test("a name list without the deny line lets an SDK under another name through (D-11) and fails", () => {
  const nameList = BLOCK.split("\n").filter((l) => !l.startsWith(NATIVE_DIR) && !l.startsWith("!")).join("\n");
  const over = Object.fromEntries(EXCLUSION_FILES.map((f) => [f, nameList]));
  withFixture(over, (root) => {
    const e = errsOf(root);
    assert.ok(e.some((x) => /does not deny by default/.test(x)));
    assert.ok(e.some((x) => x.includes(`git does not ignore ${NATIVE_DIR}/sdk-6.7.2.7020-renamed/any-file-at-all`)), e.join("\n"));
    assert.ok(e.some((x) => x.includes(`git does not ignore ${NATIVE_DIR}/unlisted-name.bin`)));
  });
});

test("the three exclusion blocks are one fact: a drifted build context fails parity", () => {
  const drifted = BLOCK.replace(/^.*\*\*\/\*\.node\n/m, "");
  withFixture({ ".dockerignore": "node_modules/\n" + drifted }, (root) => {
    const e = errsOf(root);
    assert.ok(e.some((x) => x.includes("DISAGREE") && x.includes(".dockerignore")), e.join("\n"));
  });
  withFixture({ "scripts/parity.json": JSON.stringify({ contract: "parity.v1", facts: [] }) }, (root) => {
    assert.ok(errsOf(root).some((x) => x.includes(`no "${PARITY_FACT_ID}" fact`)));
  });
  withFixture({ "scripts/parity.json": JSON.stringify({ contract: "parity.v1", facts: [{ ...FACT, sites: FACT.sites.slice(0, 2) }] }) }, (root) => {
    assert.ok(errsOf(root).some((x) => /must name exactly/.test(x)));
  });
  withFixture({ "deploy/lite/Dockerfile.lite.dockerignore": "**/node_modules\n" }, (root) => {
    assert.ok(errsOf(root).some((x) => x.includes("deploy/lite/Dockerfile.lite.dockerignore carries 0")));
  });
});

test("the block re-includes only tracked, named, non-payload source under the native directory", () => {
  const bad = BLOCK.replace(/^# <<< native-sdk-exclusion/m,
    `!${NATIVE_DIR}/libmeetingsdk.so\n!${NATIVE_DIR}/*.cpp\n!${NATIVE_DIR}/never-committed.cpp\n!core/elsewhere/file.ts\n# <<< native-sdk-exclusion`);
  withFixture(Object.fromEntries(EXCLUSION_FILES.map((f) => [f, bad])), (root) => {
    const e = errsOf(root).join("\n");
    assert.match(e, /re-includes .*libmeetingsdk\.so, which is a native meeting SDK library/);
    assert.match(e, /re-includes the pattern .*\*\.cpp/);
    assert.match(e, /never-committed\.cpp, which is not a tracked file/);
    assert.match(e, /re-includes core\/elsewhere\/file\.ts, outside/);
  });
});

test("a stock surface that names the native path fails; prose and the ignore block do not", () => {
  withFixture({
    "deploy/compose/docker-compose.yml": "services:\n  bot:\n    environment:\n      ZOOM_SDK_DIR: /opt/sdk\n",
    "core/meetings/services/bot/src/index.ts": "import { createSdkJoinSession } from '@vexa/join/node';\n",
  }, (root) => {
    const e = errsOf(root).filter((x) => x.startsWith("unreferenced:"));
    assert.ok(e.some((x) => x.includes("deploy/compose/docker-compose.yml:4") && x.includes("ZOOM_SDK_")), e.join("\n"));
    assert.ok(e.some((x) => x.includes("core/meetings/services/bot/src/index.ts:1") && x.includes("@vexa/join/node")));
    assert.ok(!e.some((x) => x.includes("README.md") || x.includes("dockerignore")), "prose and the exclusion block are not wiring");
  });
});

test("a manifest that fetches or builds the payload fails", () => withFixture({
  "core/meetings/services/bot/package.json": JSON.stringify({ name: "bot", scripts: { postinstall: "node-gyp rebuild --directory runtime/native-meeting/native" } }),
}, (root) => {
  assert.ok(errsOf(root).some((x) => x.startsWith("fetched: core/meetings/services/bot/package.json:1") && x.includes("node-gyp")));
}));

test("every linked native library has a manifest row, and the optional runtime's contracts are sealed", () => {
  withFixture({ [`${NATIVE_DIR}/binding.gyp`]: '{"targets":[{"libraries":["-lmeetingsdk","-lsomething_new","<!@(pkg-config --libs Qt5Core)"]}]}\n' }, (root) => {
    const e = errsOf(root);
    assert.equal(e.length, 1, e.join("\n"));
    assert.match(e[0], /links `something_new`, which has no row/);
  });
  withFixture({ "license-exceptions.json": JSON.stringify({ categoryB: [], operatorSupplied: [ROW] }) }, (root) => {
    assert.ok(errsOf(root).some((x) => /links `Qt5Core`, which has no row/.test(x)));
  });
  withFixture({ "contracts.seal.json": JSON.stringify({ [JOIN]: "a" }) }, (root) => {
    assert.ok(errsOf(root).some((x) => x.includes(`names contract ${CAPTURE}, which is not sealed`)));
  });
  withFixture({ "license-exceptions.json": JSON.stringify({ categoryB: [{ package: "Qt5Core", link: "Qt5Core" }], operatorSupplied: [{ ...ROW, reason: "", subprocess: "gone.cjs" }] }) }, (root) => {
    const e = errsOf(root).join("\n");
    assert.match(e, /has no `reason`/);
    assert.match(e, /subprocess gone\.cjs, which is not a tracked file/);
  });
});

test("a Makefile or shell script that fetches or builds the payload fails, as a manifest does", () => withFixture({
  "deploy/lite/Makefile": "sdk:\n\tcurl -o sdk.tar.xz https://example.invalid/zoom_meeting_sdk.tar.xz\n",
  "tools/build-native.sh": "#!/bin/sh\nnode-gyp rebuild\n",
}, (root) => {
  const e = errsOf(root);
  assert.ok(e.some((x) => x.startsWith("fetched: deploy/lite/Makefile:2")), e.join("\n"));
  assert.ok(e.some((x) => x.startsWith("fetched: tools/build-native.sh:2")), e.join("\n"));
}));

test("only the declared subprocess loads a native addon", () => withFixture({
  [`${dirname(NATIVE_DIR)}/worker.cjs`]: "const {ZoomSDK}=require(process.env.ZOOM_SDK_ADDON);\n",
  "core/meetings/services/bot/src/sneaky.ts": "const m = require('./build/Release/zoom_sdk_wrapper.node');\n",
  "tools/also.mjs": "process.dlopen(module, '/opt/x.node');\n",
}, (root) => {
  const e = errsOf(root).filter((x) => x.startsWith("reached:"));
  assert.equal(e.length, 2, e.join("\n"));
  assert.ok(e.some((x) => x.includes("core/meetings/services/bot/src/sneaky.ts:1")));
  assert.ok(e.some((x) => x.includes("tools/also.mjs:1")));
  assert.equal(checkVendorPayload(root).loaders, 1, "the subprocess's own load is counted, not refused");
}));

test("the bot manifest may name only its sanctioned mentions of the native path", () => {
  withFixture({ "core/meetings/services/bot/package.json": JSON.stringify({ name: "bot", dependencies: { "@vexa/zoom-sdk-capture": "workspace:*" },
    scripts: { test: "node --test runtime/native-meeting/test/*.test.mjs" } }, null, 2) }, (root) => {
    assert.deepEqual(errsOf(root), []);
  });
  withFixture({ "core/meetings/services/bot/package.json": JSON.stringify({ name: "bot", dependencies: { "@vexa/join": "workspace:*" },
    scripts: { start: "node runtime/native-meeting/join-probe.mjs" } }, null, 2) }, (root) => {
    assert.ok(errsOf(root).some((x) => x.startsWith("unreferenced: core/meetings/services/bot/package.json") && x.includes("native-meeting")));
  });
});

test("payload names and linked libraries are read the way the gate claims", () => {
  assert.equal(isPayload(`${NATIVE_DIR}/zoom-meeting-sdk-linux_x86_64-6.7.2.tar.xz`), "native SDK archive");
  assert.equal(isPayload("downloads/zoom-meeting-sdk.zip"), "native SDK archive");
  assert.equal(isPayload("docs/assets/diagram.zip"), null);
  assert.equal(isPayload("a/b/zoom_sdk_wrapper.node"), "compiled Node addon (.node)");
  assert.equal(isPayload("lib/libQt5Core.so.5"), "shared object (.so)");
  assert.equal(isPayload("a/libmeetingsdk.so"), "native meeting SDK library (libmeetingsdk*)");
  assert.equal(isPayload("a/zoom_wrapper.cpp"), null);
  assert.equal(isPayload("docs/solid.so.md"), null);
  assert.deepEqual(linkedLibraries('["-L<(dir)", "-lmeetingsdk", "<!@(pkg-config --libs Qt5Core)", "-lpthread"]'), ["Qt5Core", "meetingsdk", "pthread"]);
});

test("THIS repository passes: no payload, one exclusion fact, nothing stock names the native path, every link logged", () => {
  const r = checkVendorPayload(REPO);
  assert.deepEqual(r.errs, []);
  assert.ok(r.reincluded >= 0 && r.scanned > 0 && r.installers > 0);
});
