"""THE PRESET LIBRARY IS SELF-HEALING, AND ADDITIVE (2026-09-05, live, dogfood stack).

A person signed in with nowhere to go. The terminal's redeem route minted a `first-visit` scaffold —
the touch nobody sent, added to `KINDS` on 2026-09-03 — and agent-api answered:

    preset asks/first-visit.md cannot be read here (FileNotFoundError) — the link would open nothing

They landed on an empty desk, the exact state F42 ruled against. The preset was in the repo the
whole time. The INSTANCE's `_global/asks/` had been populated by hand on 2026-09-02, one day before
the file was written, and nothing could top it up: the agent-api image did not carry `behavior/` at
all, and `blank-instance.sh` deliberately keeps `asks/` across a wipe because the admin owns and may
edit those files. Every mechanism preserved the library; none supplied it.

These tests pin the four things that make it not happen again:

  1. the image SHIPS the library (the Dockerfile COPY, re-derived from the constant, not restated);
  2. the top-up ADDS what is missing, and NEVER overwrites what is already there;
  3. `read_preset` looks in `_global` first and the image second, and empties still fail;
  4. a `first-visit` mint succeeds against a library that does not have the file.
"""
from __future__ import annotations

import pathlib

import pytest

from control_plane import preset_library, scaffolds

REPO = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_ASKS = REPO / "behavior" / "asks"
DOCKERFILE = REPO / "core" / "agent" / "services" / "agent-api" / "Dockerfile"

ADMIN_EDIT = "---\nlabel: the admin's own words\n---\nDo it the way WE do it.\n"
SHIPPED = "---\nlabel: as shipped\n---\nDo the thing.\n"


# ── 1 · the image ships it ───────────────────────────────────────────────────────────────────────

def test_the_agent_api_image_copies_the_preset_library():
    """Before this line existed the image carried no `behavior/` at all, so no deploy could ever put
    a new preset in front of a running instance. Derived from `DEFAULT_IMAGE_ASKS_DIR` rather than
    restated: change the constant without changing the Dockerfile and this fails."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dest = preset_library.DEFAULT_IMAGE_ASKS_DIR            # e.g. /app/behavior/asks
    # The image's WORKDIR is /app and every COPY in this Dockerfile writes `./<dst>`.
    assert dest.startswith("/app/"), "the library's in-image home must sit under the image WORKDIR"
    rel = dest[len("/app/"):]
    wanted = f"COPY behavior/asks ./{rel}"
    assert wanted in dockerfile, (
        f"the agent-api image must COPY the preset library to {dest} — expected the line "
        f"{wanted!r} in {DOCKERFILE.relative_to(REPO)}. Without it `_global/asks/` can only ever "
        f"be topped up by hand, which is how a first-time visitor met an empty desk on 2026-09-05.")


def test_every_preset_a_scaffold_kind_can_open_with_is_actually_in_the_repo_library():
    """`first-visit.md` is the one that was missing live. The catalogue and the library are two
    lists that must not drift; this is the half a unit test can check."""
    assert (BEHAVIOR_ASKS / "first-visit.md").is_file()
    assert (BEHAVIOR_ASKS / "README.md").is_file(), \
        "the README is what tells an admin these files are theirs to edit — it ships too"


# ── 2 · the top-up is additive ───────────────────────────────────────────────────────────────────

def _image(tmp_path: pathlib.Path, *names: str) -> pathlib.Path:
    """A stand-in for the library baked into the image: the named presets, as shipped."""
    d = tmp_path / "image-asks"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).write_text(SHIPPED)
    return d


def test_it_adds_a_preset_the_store_does_not_have(tmp_path):
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    (g / "asks" / "prep.md").write_text(SHIPPED)

    added = preset_library.top_up(g, _image(tmp_path, "prep.md", "first-visit.md"))

    assert added == ["first-visit.md"]
    assert (g / "asks" / "first-visit.md").read_text() == SHIPPED


def test_it_never_overwrites_a_preset_the_admin_has_edited(tmp_path):
    """EXISTENCE IS THE WHOLE TEST — not mtime, not size, not content. An admin edit changes how
    every agent in the deployment behaves; a deploy that could silently revert one would be worse
    than the gap this closes."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    (g / "asks" / "prep.md").write_text(ADMIN_EDIT)

    added = preset_library.top_up(g, _image(tmp_path, "prep.md"))

    assert added == []
    assert (g / "asks" / "prep.md").read_text() == ADMIN_EDIT


def test_an_admin_edit_survives_even_when_other_presets_are_added_around_it(tmp_path):
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    (g / "asks" / "prep.md").write_text(ADMIN_EDIT)

    added = preset_library.top_up(
        tmp_path / "_global",
        _image(tmp_path, "prep.md", "first-visit.md", "catch-up.md"))

    assert added == ["catch-up.md", "first-visit.md"]
    assert (g / "asks" / "prep.md").read_text() == ADMIN_EDIT


def test_it_is_idempotent(tmp_path):
    """A restart is not an event. The second run must add nothing and say so."""
    g = tmp_path / "_global"
    img = _image(tmp_path, "first-visit.md")
    assert preset_library.top_up(g, img) == ["first-visit.md"]
    assert preset_library.top_up(g, img) == []


def test_it_creates_the_library_on_an_instance_that_has_none(tmp_path):
    g = tmp_path / "_global"
    g.mkdir()
    assert preset_library.top_up(g, _image(tmp_path, "first-visit.md")) == ["first-visit.md"]
    assert (g / "asks").is_dir()


def test_a_read_only_global_is_reported_not_raised(tmp_path):
    """A `_global` mounted read-only is a legitimate deployment shape (compose binds the host-path
    mirror `:ro`). It must not stop the service from booting — the same reason `ensure_repo` is
    best-effort at the same call site. The fallback in `read_preset` is what holds instead."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    g.chmod(0o500)
    (g / "asks").chmod(0o500)
    try:
        assert preset_library.top_up(g, _image(tmp_path, "first-visit.md")) == []
    finally:
        (g / "asks").chmod(0o700)
        g.chmod(0o700)


def test_a_build_with_no_library_tops_up_nothing(tmp_path, monkeypatch):
    """An older image predates the COPY line, and a test process is not an image at all. `None` is a
    real answer, and it means exactly today's behaviour."""
    monkeypatch.setenv(preset_library.ENV_IMAGE_ASKS_DIR, str(tmp_path / "nope"))
    assert preset_library.image_asks_dir() is None
    assert preset_library.top_up(tmp_path / "_global") == []


def test_subdirectories_and_dotfiles_are_not_the_library(tmp_path):
    """`read_preset` resolves exactly one segment, `asks/<name>.md`. Anything nested could not be
    named by a scaffold's `opening`, and copying it would put files in the admin's view that the
    product cannot read."""
    img = _image(tmp_path, "first-visit.md")
    (img / ".hidden").write_text("x")
    (img / "nested").mkdir()
    (img / "nested" / "deep.md").write_text("x")

    assert preset_library.top_up(tmp_path / "_global", img) == ["first-visit.md"]
    assert not (tmp_path / "_global" / "asks" / "nested").exists()
    assert not (tmp_path / "_global" / "asks" / ".hidden").exists()


def test_the_directory_name_is_the_one_scaffolds_joins(tmp_path):
    """Two spellings of one path is how a library ends up written where nothing reads it."""
    assert scaffolds.preset_path(tmp_path, "prep") == \
        tmp_path / preset_library.ASKS_DIRNAME / "prep.md"


# ── 3 · the lookup order ─────────────────────────────────────────────────────────────────────────

def test_the_store_wins_over_the_image(tmp_path):
    """The admin's file is looked at FIRST and therefore always wins — that is what admin-owned
    means, and it is why the top-up may be additive without losing anything."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    (g / "asks" / "prep.md").write_text(ADMIN_EDIT)

    fm, body = scaffolds.read_preset(g, "prep", image_root=_image(tmp_path, "prep.md"))

    assert fm["label"] == "the admin's own words"
    assert "WE do it" in body


def test_the_image_answers_when_the_store_is_merely_behind(tmp_path):
    """The 2026-09-05 case exactly: the preset is in the build and not on the store."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)

    fm, body = scaffolds.read_preset(g, "first-visit",
                                     image_root=_image(tmp_path, "first-visit.md"))

    assert fm["label"] == "as shipped"
    assert "Do the thing" in body


def test_a_name_that_exists_nowhere_still_fails_at_mint(tmp_path):
    """UNCHANGED, and load-bearing: a mint whose preset does not exist must fail at MINT, where a
    step can still refuse to send, rather than at click, where a person meets an empty chat. Only
    the definition of "does not exist" widened from one root to two."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    with pytest.raises(scaffolds.ScaffoldError) as e:
        scaffolds.read_preset(g, "no-such-preset", image_root=_image(tmp_path, "prep.md"))
    assert "cannot be read here" in str(e.value)


def test_with_no_image_root_the_failure_is_exactly_the_old_one(tmp_path):
    """`image_root=None` means "look in `_global` and nowhere else" — the pre-fallback behaviour,
    pinned so the fallback can be shown to be the only thing that changed."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    with pytest.raises(scaffolds.ScaffoldError) as e:
        scaffolds.read_preset(g, "first-visit", image_root=None)
    assert "asks/first-visit.md cannot be read here (FileNotFoundError)" in str(e.value)


def test_an_empty_preset_on_the_store_does_NOT_fall_through_to_the_image(tmp_path):
    """ABSENCE FALLS THROUGH, EMPTINESS DOES NOT. A blank file in `_global` is a present-but-broken
    ADMIN file; answering it with the image's copy would be a deploy overruling a human edit — the
    same thing the additive top-up refuses to do one layer up."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    (g / "asks" / "prep.md").write_text("   \n")
    with pytest.raises(scaffolds.ScaffoldError) as e:
        scaffolds.read_preset(g, "prep", image_root=_image(tmp_path, "prep.md"))
    assert "is empty" in str(e.value)


def test_the_fallback_cannot_be_walked_out_of(tmp_path):
    """The name is validated before either root is joined, so a second lookup root is not a second
    chance at traversal."""
    for junk in ("../../etc/passwd", "a/b", ".", "", "x" * 65):
        with pytest.raises(scaffolds.ScaffoldError):
            scaffolds.read_preset(tmp_path, junk, image_root=_image(tmp_path, "prep.md"))


# ── 4 · the whole loop, on the repo's real library ───────────────────────────────────────────────

def test_first_visit_mints_against_a_library_that_does_not_have_it(tmp_path):
    """END TO END, with the repo's own `behavior/asks/` standing in for the image's copy: an
    instance whose `_global/asks/` predates `first-visit.md` — the live 2026-09-05 shape — is topped
    up, and the preset reads. Both halves are exercised: after the top-up the file is ON THE STORE,
    where an admin can see and edit it, which a fallback alone would never achieve."""
    g = tmp_path / "_global"
    (g / "asks").mkdir(parents=True)
    for name in ("prep.md", "catch-up.md"):          # the 2026-09-02 library, minus the new file
        (g / "asks" / name).write_text(ADMIN_EDIT)

    with pytest.raises(scaffolds.ScaffoldError):     # before: the live failure
        scaffolds.read_preset(g, "first-visit", image_root=None)

    added = preset_library.top_up(g, BEHAVIOR_ASKS)

    assert "first-visit.md" in added
    assert (g / "asks" / "first-visit.md").is_file()
    _fm, body = scaffolds.read_preset(g, "first-visit", image_root=None)
    assert body.strip()
    # and the two files that were already there are still the admin's
    for name in ("prep.md", "catch-up.md"):
        assert (g / "asks" / name).read_text() == ADMIN_EDIT
