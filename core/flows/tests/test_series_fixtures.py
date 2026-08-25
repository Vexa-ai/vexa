"""The series fixture library, proven offline.

A fixture library that nobody validates rots into a pile of files that look like data. These
tests are cheap and they check the things that actually break the iteration loop: a manifest that
lies about which episodes exist, a transcript line that is not JSON, an episode with no ground
truth to judge against, a fixture that grew past what belongs in a git repo — and, the one that
matters most, an INVENTED SPEAKER. Auto-captions carry no diarization; if a fixture ever gains
speaker labels the source did not have, the harness is lying to the behavior it exists to test,
so the manifest has to declare `speakers: "labelled" | "none"` and the fixture must match it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SERIES_DIR = Path(__file__).resolve().parent / "series"
MAX_TOTAL_BYTES = 5 * 1024 * 1024
REQUIRED_SEG_KEYS = {"start", "end", "text", "speaker", "language"}


def series_slugs() -> list[str]:
    if not SERIES_DIR.is_dir():
        return []
    return sorted(d.name for d in SERIES_DIR.iterdir()
                  if d.is_dir() and (d / "series.json").is_file())


def manifest(slug: str) -> dict:
    return json.loads((SERIES_DIR / slug / "series.json").read_text())


def test_library_is_not_empty():
    assert series_slugs(), f"no series fixtures under {SERIES_DIR}"


@pytest.mark.parametrize("slug", series_slugs())
def test_manifest_shape(slug):
    m = manifest(slug)
    for key in ("slug", "title", "language", "source", "episodes"):
        assert key in m, f"{slug}/series.json missing '{key}'"
    assert m["slug"] == slug
    assert m["speakers"] in ("labelled", "none"), \
        "declare whether the source carried speaker labels — 'none' is the honest answer for " \
        "auto-captions and the fixture is checked against it"
    ns = [e["n"] for e in m["episodes"]]
    assert ns == list(range(1, len(ns) + 1)), f"{slug}: episodes must be consecutive from 1, got {ns}"
    assert len(ns) >= 3, f"{slug}: a series needs >=3 consecutive episodes to be longitudinal"


@pytest.mark.parametrize("slug", series_slugs())
def test_every_episode_parses_and_matches_its_declaration(slug):
    m = manifest(slug)
    for ep in m["episodes"]:
        f = SERIES_DIR / slug / ep["transcript"]
        assert f.is_file(), f"{slug} ep{ep['n']}: missing {f}"
        segs = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        assert segs, f"{slug} ep{ep['n']}: empty transcript"
        for i, s in enumerate(segs):
            assert REQUIRED_SEG_KEYS <= set(s), \
                f"{slug} ep{ep['n']} line {i + 1}: keys {sorted(set(s))} — the fixture shape is " \
                f"{sorted(REQUIRED_SEG_KEYS)} (the transcriptions-table columns)"
            assert isinstance(s["text"], str) and s["text"].strip()
            assert s["end"] >= s["start"]
        labelled = any(s["speaker"] for s in segs)
        assert labelled == (m["speakers"] == "labelled"), (
            f"{slug} ep{ep['n']}: manifest says speakers={m['speakers']!r} but the fixture "
            f"{'has' if labelled else 'has no'} labels. An invented speaker is the one lie this "
            "library must never tell.")


@pytest.mark.parametrize("slug", series_slugs())
def test_every_episode_has_ground_truth_and_provenance(slug):
    m = manifest(slug)
    assert (SERIES_DIR / slug / "README.md").is_file(), f"{slug}: no README (sources, dates, GT)"
    for ep in m["episodes"]:
        assert ep.get("video_url"), f"{slug} ep{ep['n']}: no source URL — provenance is not optional"
        gt = SERIES_DIR / slug / "ground-truth" / f"ep{ep['n']}.md"
        assert gt.is_file(), f"{slug} ep{ep['n']}: no ground truth at {gt} — nothing to judge against"
        body = gt.read_text()
        assert "## Entities" in body, \
            f"{gt}: needs an '## Entities' section — that list is what `judge`'s presence check reads"


def test_library_stays_small_enough_to_live_in_git():
    total = sum(f.stat().st_size for f in SERIES_DIR.rglob("*") if f.is_file())
    assert total < MAX_TOTAL_BYTES, \
        f"series fixtures total {total / 1024 / 1024:.1f} MB (cap {MAX_TOTAL_BYTES / 1024 / 1024} MB) — trim episodes"


@pytest.mark.parametrize("slug", series_slugs())
def test_the_harness_can_load_and_render_every_episode(slug):
    """The fixture and the harness agree on the shape — including the rendering that lands on the
    `meeting.completed` fact, `speaker: text` per line, `?` where the source had no label."""
    import sys
    sys.path.insert(0, str(SERIES_DIR.parent.parent / "witness"))
    from series_run import load_episode, transcript_text  # noqa: PLC0415

    for ep in manifest(slug)["episodes"]:
        segs = load_episode(slug, ep)
        text = transcript_text(segs)
        assert text.count("\n") == len(segs) - 1
        assert len(text) > 500, f"{slug} ep{ep['n']}: {len(text)} chars — too thin to scaffold from"
