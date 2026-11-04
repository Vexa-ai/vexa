"""pytest_shard: run one shard of a package's own pytest population.

gate:python loads this, and only for a `<dir>@<k>/<n>` selection (`node scripts/gates.mjs python
core/agent@2/3`), which is how the `python` matrix in .github/workflows/gates.yml splits a package
too slow to run in one job. It is put on the path with PYTHONPATH=scripts/pytest-shard and loaded
with `-p pytest_shard --vexa-shard=<k>/<n>`; no package declares it and nothing else imports it.

A shard is a set of test FILES, chosen by a hash of each file's path relative to the package root:

  - every shard collects the whole population, exactly as a plain `uv run pytest -q` does, and keeps
    only the files that hash to it, so shards 1..n partition what pytest itself collected. No file
    can fall between two shards and no file can run in two;
  - a file's shard depends on its own path only, never on which other files exist, so a new or a
    deleted test file moves nothing else;
  - a module's tests stay together in one process, in their own order, with their module fixtures.

A shard that keeps nothing fails (pytest's "no tests ran", exit 5) rather than passing empty.
"""

import os
import zlib

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--vexa-shard",
        default=None,
        metavar="K/N",
        help="run only the test files whose path hashes to shard K of N (gate:python's CI matrix)",
    )


def _parse(spec):
    try:
        k, n = (int(part) for part in spec.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--vexa-shard={spec!r}: expected K/N, e.g. 2/4")
    if n < 1 or not 1 <= k <= n:
        raise pytest.UsageError(f"--vexa-shard={spec!r}: shard K must be within 1..N")
    return k, n


def shard_of(relpath, n):
    """The shard (1..n) that the test file at `relpath` (POSIX, relative to the package root) runs in."""
    return zlib.crc32(relpath.encode("utf-8")) % n + 1


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--vexa-shard")
    if not spec:
        return
    k, n = _parse(spec)
    root = str(config.rootpath)
    keep, drop = [], []
    for item in items:
        rel = os.path.relpath(str(item.path), root).replace(os.sep, "/")
        (keep if shard_of(rel, n) == k else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
