"""THE AGENT-API IMAGE SHIPS EVERY FILE ITS OWN CODE READS BY PATH (F221).

`routers/health.py` serves `GET /.well-known/mcp-tools.json` from a path it resolves at import
time: ``Path(__file__).resolve().parents[2] / "mcp.tools.v1.json"`` — two levels above
``routers/``, landing on ``core/agent/mcp.tools.v1.json`` (the domain's MCP tool manifest, ADR-0037
/ PRD decision 40.5). The `core/agent/services/agent-api/Dockerfile` COPYs `shared`, `control_plane`,
`contracts` and five schema files into the image — never `core/agent/mcp.tools.v1.json` itself, which
sits at the `core/agent/` root, outside every copied directory.

Live result (found by the dogfood hop, 2026-09-03): the built agent-api image 503s
`GET /.well-known/mcp-tools.json` with "this build carries no tool manifest", the assembled MCP edge
lists 0 agent tools, and a worker dispatched through it cannot leave the rig.

THIS TEST DOES NOT HARDCODE THE FILENAME. It re-derives the requirement from source: it scans every
`.py` file under `control_plane/` for the `Path(__file__).resolve().parent[s[N]]? / "<name>"` idiom
health.py uses, resolves each match to the exact repo file it names, and asserts that file is covered
by the Dockerfile's own COPY set (parsed, not restated). A future module that reads a new file this
same way is caught here automatically — the check does not need to be told the file exists.

RED before the fix: `core/agent/mcp.tools.v1.json` is required (by health.py) and NOT covered by any
COPY source in the Dockerfile → `test_dockerfile_copies_every_by_path_read_file` fails, naming the
exact missing path. `config_preflight.py`'s own by-path read (`config.v1.json`, sitting next to it
inside `control_plane/`) is ALREADY covered by the wholesale `COPY core/agent/control_plane
./control_plane` — proving the test discriminates real gaps from files that ride along for free,
rather than failing (or passing) everything indiscriminately.
"""
from __future__ import annotations

import pathlib
import re

# tests/ -> agent/ -> core/ -> repo root (same anchor test_worker_allowlist_manifested.py uses).
REPO = pathlib.Path(__file__).resolve().parents[3]
CONTROL_PLANE_DIR = REPO / "core" / "agent" / "control_plane"
DOCKERFILE = REPO / "core" / "agent" / "services" / "agent-api" / "Dockerfile"

#: Matches `Path(__file__).resolve().parent / "name"` (bare `.parent`, i.e. `parents[0]`) and
#: `Path(__file__).resolve().parents[N] / "name"`. Captures the optional `[N]` and the literal
#: filename joined onto it — exactly the idiom both `health.py` and `config_preflight.py` use to
#: resolve a file that lives alongside (or a fixed number of directories above) themselves.
BY_PATH_READ = re.compile(
    r"Path\(__file__\)\.resolve\(\)\.parent(?:s\[(\d+)\])?\s*/\s*\"([^\"]+)\""
)


def _by_path_requirements() -> dict[pathlib.Path, str]:
    """Every (source .py file, resolved repo-relative required path) pair found under control_plane/.

    Returns a dict keyed by the resolved required path (deduped) mapping to a description naming
    which source file demanded it, for a legible failure message.
    """
    requirements: dict[pathlib.Path, str] = {}
    for py_file in sorted(CONTROL_PLANE_DIR.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for match in BY_PATH_READ.finditer(text):
            depth = int(match.group(1)) if match.group(1) else 0
            name = match.group(2)
            # py_file.resolve().parents[depth]: depth=0 is the directory containing py_file itself
            # (bare `.parent`), matching how `Path(__file__).resolve().parents[N]` walks up from a
            # FILE path — parents[0] is already "the directory this file lives in".
            base_dir = py_file.resolve().parents[depth]
            required = (base_dir / name).resolve()
            requirements[required] = f"{py_file.relative_to(REPO)} reads {required.relative_to(REPO)}"
    return requirements


def _copy_sources(dockerfile_text: str) -> list[str]:
    """Every source path named in a `COPY <src...> <dst>` instruction, repo-root-relative.

    The agent-api Dockerfile's build context is the repo root (compose `context: ../..`) and every
    COPY in it is `COPY <repo-relative-src...> ./<dst>` — no `--from=`, no multi-stage, no glob. This
    parser only needs to handle that one shape."""
    sources: list[str] = []
    for line in dockerfile_text.splitlines():
        line = line.strip()
        if not line.startswith("COPY "):
            continue
        parts = line[len("COPY "):].split()
        if len(parts) < 2:
            continue
        *srcs, _dst = parts
        sources.extend(srcs)
    return sources


def _covered(required_repo_rel: pathlib.PurePosixPath, sources: list[str]) -> bool:
    """True if some COPY source is `required_repo_rel` itself, or a directory containing it."""
    for src in sources:
        s = pathlib.PurePosixPath(src)
        if required_repo_rel == s:
            return True
        try:
            required_repo_rel.relative_to(s)
            return True
        except ValueError:
            continue
    return False


def test_by_path_requirements_scan_finds_the_known_reads():
    """Pin the scan itself: it must find both known by-path reads, or the regex has gone stale."""
    required = {p.relative_to(REPO).as_posix() for p in _by_path_requirements()}
    assert "core/agent/mcp.tools.v1.json" in required
    assert "core/agent/control_plane/config.v1.json" in required


def test_dockerfile_copies_every_by_path_read_file():
    """Every file control_plane/*.py resolves by path must be in the agent-api image (F221)."""
    sources = _copy_sources(DOCKERFILE.read_text(encoding="utf-8"))
    missing = []
    for required_abs, description in _by_path_requirements().items():
        required_rel = pathlib.PurePosixPath(required_abs.relative_to(REPO).as_posix())
        if not _covered(required_rel, sources):
            missing.append(description)
    assert not missing, (
        "agent-api Dockerfile does not COPY a file its own code reads by path — this 503s at "
        f"runtime (F221): {missing}. Add a COPY line for it in {DOCKERFILE.relative_to(REPO)}."
    )
