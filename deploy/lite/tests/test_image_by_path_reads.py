"""The Lite image ships every file its Python services read next to their own code.

A service that resolves a file from its own location — ``Path(__file__).resolve().parents[N] /
"name"`` or ``Path(__file__).with_name("name")`` — finds it only if the image put that file at the
same place relative to the code. The agent-api image COPYs ``core/agent/mcp.tools.v1.json`` for
``routers/health.py``; the Lite image, which roots the agent tree at ``/app/agent``, did not, so
Lite's agent-api answered 503 for its tool manifest and Lite's MCP service crash-looped waiting on it.

This test derives the requirement from source and from ``Dockerfile.lite`` itself: it walks every
Python file the image COPYs, finds those reads, maps each to the path it resolves to INSIDE the image
(using the COPY instructions as written) and asserts some COPY puts a repo file there. A new read of
this shape is caught without the test being told about it.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "deploy" / "lite" / "Dockerfile.lite"

PARENTS_READ = re.compile(r"Path\(__file__\)\.resolve\(\)\.parent(?:s\[(\d+)\])?((?:\s*/\s*\"[^\"]+\")+)")
WITH_NAME_READ = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?\.with_name\(\s*\"([^\"]+)\"\s*\)")
SEGMENT = re.compile(r"\"([^\"]+)\"")


def _copies() -> list[tuple[str, str, bool]]:
    """``(repo source, image destination, destination is a directory)`` for every COPY from the build
    context to an absolute path (the final image; the build stages copy to relative paths)."""
    out = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        parts = line[len("COPY "):].split()
        *srcs, dst = parts
        if not dst.startswith("/"):
            continue
        many = len(srcs) > 1 or dst.endswith("/")
        out.extend((s.rstrip("/"), dst.rstrip("/"), many) for s in srcs)
    return out


def _landings(repo_file: Path, copies) -> list[str]:
    """Where ``repo_file`` lands in the image."""
    rel = repo_file.relative_to(ROOT).as_posix()
    out = []
    for src, dst, many in copies:
        if (ROOT / src).is_dir():
            if rel.startswith(src + "/"):
                out.append(posixpath.join(dst, rel[len(src) + 1:]))
        elif rel == src:
            out.append(posixpath.join(dst, posixpath.basename(src)) if many else dst)
    return out


def _provided(image_path: str, copies) -> bool:
    for src, dst, many in copies:
        if (ROOT / src).is_dir():
            if image_path.startswith(dst + "/") and (ROOT / src / image_path[len(dst) + 1:]).is_file():
                return True
        elif image_path == (posixpath.join(dst, posixpath.basename(src)) if many else dst):
            return True
    return False


def _reads(py: Path) -> list[tuple[int, str]]:
    """``(levels up from the file's directory, relative path)`` for each read of this shape."""
    text = py.read_text(encoding="utf-8", errors="replace")
    found = [(int(m.group(1) or 0), "/".join(SEGMENT.findall(m.group(2)))) for m in PARENTS_READ.finditer(text)]
    found += [(0, m.group(1)) for m in WITH_NAME_READ.finditer(text)]
    return found


def _requirements():
    copies = _copies()
    shipped = set()
    for src, _dst, _many in copies:
        p = ROOT / src
        shipped.update(p.rglob("*.py") if p.is_dir() else ([p] if p.suffix == ".py" else []))
    for py in sorted(shipped):
        for levels, rel in _reads(py):
            for landed in _landings(py, copies):
                base = posixpath.dirname(landed)
                for _ in range(levels):
                    base = posixpath.dirname(base)
                yield py.relative_to(ROOT).as_posix(), posixpath.join(base, rel)


def test_the_scan_sees_the_reads_it_exists_for():
    required = {img for _src, img in _requirements()}
    assert "/app/agent/mcp.tools.v1.json" in required
    assert "/app/runtime/src/runtime_kernel/config.v1.json" in required


def test_every_file_a_service_reads_beside_its_code_is_in_the_image():
    copies = _copies()
    missing = sorted({f"{src} reads {img}" for src, img in _requirements() if not _provided(img, copies)})
    assert not missing, (
        "Dockerfile.lite does not put a file where the code that reads it looks for it: "
        f"{missing}. Add a COPY to that path.")
