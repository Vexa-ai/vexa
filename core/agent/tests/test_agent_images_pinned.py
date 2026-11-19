"""The agent images (worker, agent-api, credential broker) build from pinned inputs only (D-3, D-4).

Every base and every ``COPY --from`` image is pinned by digest, nothing is fetched and piped into a
shell, and the ``uv`` the image installs is past the advisories against 0.9.x (fixed in 0.11.15).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
DOCKERFILES = [AGENT / "worker" / "Dockerfile", AGENT / "services" / "agent-api" / "Dockerfile",
               AGENT / "services" / "credential-broker" / "Dockerfile"]
_IMAGE_REF = re.compile(r"^\s*(?:FROM|COPY\s+--from=)\s*(\S+)", re.M | re.I)


def _logical_lines(text: str) -> str:
    return re.sub(r"\\\n", " ", text)


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: str(p.relative_to(AGENT)))
def test_every_image_an_agent_image_builds_from_is_pinned_by_digest(path):
    refs = _IMAGE_REF.findall(_logical_lines(path.read_text()))
    assert refs
    assert [r for r in refs if "@sha256:" not in r] == []


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: str(p.relative_to(AGENT)))
def test_nothing_is_fetched_into_a_shell(path):
    assert not re.search(r"(curl|wget)[^\n|]*\|\s*(ba|z)?sh\b", _logical_lines(path.read_text()))


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: str(p.relative_to(AGENT)))
def test_uv_is_past_its_advisories(path):
    versions = re.findall(r"uv==(\d+)\.(\d+)\.(\d+)", path.read_text())
    assert versions
    assert all(tuple(map(int, v)) >= (0, 11, 15) for v in versions)
