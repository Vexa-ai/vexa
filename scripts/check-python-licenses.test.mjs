// Unit tests for the Python half of gate:licenses (scripts/check-python-licenses.mjs).
// Run: node --test scripts/check-python-licenses.test.mjs
// The gate-level reds (a planted Dockerfile or index edit through the real gate) live in
// scripts/gates.test.mjs beside the other gate:licenses tests.

import test from "node:test";
import assert from "node:assert/strict";
import { lockClosure, markerApplies, parseUvLock, pythonInstalls, spdxFromPyPI, installKey } from "./check-python-licenses.mjs";
import { guardTree } from "./test-tree.mjs";

guardTree();

const LOCK = `version = 1
revision = 3
requires-python = ">=3.11"

[[package]]
name = "app"
version = "0.1.0"
source = { virtual = "." }
dependencies = [
    { name = "uvicorn", extra = ["standard"] },
    { name = "colorama", marker = "sys_platform == 'win32'" },
    { name = "tomli", marker = "python_full_version < '3.11'" },
    { name = "typing-extensions", marker = "python_full_version < '3.13'" },
]

[package.dev-dependencies]
dev = [
    { name = "pytest" },
]
control-plane = [
    { name = "fastapi" },
]

[package.metadata]
requires-dist = [{ name = "uvicorn", extras = ["standard"] }]

[[package]]
name = "uvicorn"
version = "0.34.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "h11" },
]
wheels = [
    { url = "https://example/uvicorn.whl", hash = "sha256:00" },
]

[package.optional-dependencies]
standard = [
    { name = "uvloop", marker = "platform_python_implementation != 'PyPy' and sys_platform != 'cygwin' and sys_platform != 'win32'" },
    { name = "colorama", marker = "sys_platform == 'win32'" },
]

[[package]]
name = "h11"
version = "0.16.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "uvloop"
version = "0.21.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "colorama"
version = "0.4.6"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "tomli"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "typing-extensions"
version = "4.15.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "9.0.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "fastapi"
version = "0.120.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "h11" }]
`;

test("the production closure follows extras and drops what no Linux CPython target installs", () => {
  const got = [...lockClosure(parseUvLock(LOCK))].sort();
  assert.deepEqual(got, ["h11==0.16.0", "typing-extensions==4.15.0", "uvicorn==0.34.0", "uvloop==0.21.0"]);
});

test("the dev group stays out unless named; a named group is added", () => {
  const pkgs = parseUvLock(LOCK);
  assert.ok(![...lockClosure(pkgs)].some((k) => k.startsWith("pytest")));
  assert.ok([...lockClosure(pkgs, ["control-plane"])].includes("fastapi==0.120.0"));
  assert.throws(() => lockClosure(pkgs, ["nope"]), /dependency group "nope"/);
});

test("markers: Windows-only and old-Python-only deps are dropped, version gates are numeric", () => {
  assert.equal(markerApplies("sys_platform == 'win32'"), false);
  assert.equal(markerApplies("python_full_version < '3.11'"), false);
  assert.equal(markerApplies("python_full_version < '3.11.3'"), false);
  assert.equal(markerApplies("python_full_version < '3.13'"), true);
  assert.equal(markerApplies("python_full_version >= '3.12' and sys_platform == 'emscripten'"), false);
  assert.equal(markerApplies("platform_machine == 'aarch64' or platform_machine == 'x86_64'"), true);
  assert.equal(markerApplies("(sys_platform == 'darwin') or (implementation_name != 'pypy')"), true);
  assert.equal(markerApplies("some_unknown_var == 'x'"), true, "an unknown variable must keep the dep (fail safe)");
});

const DOCKERFILE = `FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.9.22
COPY core/svc/pyproject.toml core/svc/uv.lock ./
# a comment inside the recipe
RUN --mount=type=cache,target=/root/.cache/uv \\
    uv sync --frozen --no-install-project --no-dev
RUN uv pip install --python /opt/venv/bin/python "uvicorn[standard]==0.34.0" "redis==5.2.1"
COPY pyproject.toml uv.lock /tmp/other/
RUN cd /tmp/other && UV_PROJECT_ENVIRONMENT=/opt/o uv sync --frozen --no-default-groups --group control-plane \\
 && uv sync --frozen
`;

test("recipes: each uv sync is bound to the lock COPY'd before it, with its groups and dev flag", () => {
  const ins = pythonInstalls("deploy/x/Dockerfile", DOCKERFILE);
  assert.deepEqual(ins.map((i) => [i.kind, i.lockDir, i.kind === "sync" ? i.groups : i.specs, i.kind === "sync" ? i.installsDev : null]), [
    ["pip", null, ["uv==0.9.22"], null],
    ["sync", "core/svc", [], false],
    ["pip", "core/svc", ["uvicorn[standard]==0.34.0", "redis==5.2.1"], null],
    ["sync", "deploy/x", ["control-plane"], false],
    ["sync", "deploy/x", [], true],
  ]);
  assert.equal(installKey("core/svc", ["uvicorn[standard]==0.34.0", "redis==5.2.1"]), "core/svc :: redis==5.2.1 uvicorn[standard]==0.34.0");
});

test("PyPI metadata → SPDX: expression first, then a recognisable licence field, then classifiers (AND)", () => {
  assert.equal(spdxFromPyPI({ license_expression: "Apache-2.0 OR BSD-3-Clause" }), "Apache-2.0 OR BSD-3-Clause");
  assert.equal(spdxFromPyPI({ license: "MPL-2.0 AND MIT" }), "MPL-2.0 AND MIT");
  assert.equal(spdxFromPyPI({ license: "MIT No Attribution" }), "MIT-0");
  assert.equal(spdxFromPyPI({ license: "BSD 3-Clause License" }), "BSD-3-Clause");
  assert.equal(spdxFromPyPI({ license: "Copyright (c) 2005, NumPy Developers.\nAll rights reserved.", classifiers: ["License :: OSI Approved :: BSD License"] }), "BSD");
  assert.equal(spdxFromPyPI({ license: "Dual License", classifiers: ["License :: OSI Approved :: Apache Software License", "License :: OSI Approved :: BSD License"] }), "Apache-2.0 AND BSD");
  assert.equal(spdxFromPyPI({ license: "", classifiers: ["License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"] }), "LGPL-3.0-only");
  assert.match(spdxFromPyPI({ license: "", classifiers: [] }), /^UNKNOWN/);
});
