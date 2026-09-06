"""`_global` ARRIVES SCAFFOLDED, AND THE SCAFFOLD CANNOT LIFT THE GATE.

Two halves, and the second is the one that would have been missed.

  1. The seed is copied in, ADDITIVELY: a file the store does not have is added, a file it does have
     is the admin's and is never touched. Same rule as `preset_library`, one directory up and
     recursive (the seed has `flows/` under it).

  2. A seeded layer file is NOT a written one. Before the seed, `_global` was an empty directory and
     EMPTINESS was the whole test — a file with nothing in it had not been written. A placeholder
     is not empty, so without a second rule an admin could have accepted five files nobody filled
     in, and `mark_global_ready` would have opened the instance on them.

The marker is POSITIVE EVIDENCE, not a comparison against remembered bytes: a hash of the shipped
file goes stale the moment the seed's wording changes and then calls an untouched placeholder
"written". `vexa:unwritten` is a signature we always emit and an editor cannot remove by accident.
"""
from __future__ import annotations

import pathlib

from control_plane import global_layer, global_seed

REPO = pathlib.Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "core" / "agent" / "services" / "agent-api" / "Dockerfile"
SEED_IN_REPO = REPO / "behavior" / "global"
MAIL_IN_REPO = REPO / "behavior" / "mail"


# ── the image actually ships it ──────────────────────────────────────────────────────────────────

def _copies_to(dest: str) -> bool:
    """Does the agent-api Dockerfile COPY something to `dest` (an /app-rooted in-image path)?

    Derived from the module's constants rather than restated, exactly as `test_preset_library.py`
    derives its own: change a constant without changing the Dockerfile and this goes red."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    rel = dest[len("/app/"):] if dest.startswith("/app/") else dest.lstrip("/")
    return any(line.split()[-1].lstrip("./") == rel
               for line in text.splitlines() if line.strip().startswith("COPY "))


def test_the_agent_api_image_ships_the_organisation_tier_seed():
    assert SEED_IN_REPO.is_dir(), f"{SEED_IN_REPO} is the seed — it has to exist in the repo"
    assert _copies_to(global_seed.DEFAULT_IMAGE_SEED_DIR), (
        f"the agent-api Dockerfile does not COPY behavior/global to "
        f"{global_seed.DEFAULT_IMAGE_SEED_DIR}: a fresh instance's _global stays an empty directory "
        f"and the setup conversation has to compose the structure itself")


def test_the_agent_api_image_ships_the_mail_templates():
    assert _copies_to(global_seed.DEFAULT_IMAGE_MAIL_DIR), (
        f"the agent-api Dockerfile does not COPY behavior/mail to "
        f"{global_seed.DEFAULT_IMAGE_MAIL_DIR}: _global/mail/ then reaches an instance only when "
        f"somebody remembers to carry it across, which is the failure preset_library exists for")


def test_the_seed_carries_the_whole_layer():
    names = {p.name for p in SEED_IN_REPO.iterdir() if p.is_file()}
    for required in global_layer.LAYER_FILES:
        assert required in names, f"behavior/global/{required} is missing from the seed"


# ── additive copy-in ─────────────────────────────────────────────────────────────────────────────

def _seed(tmp_path, tree: dict) -> list:
    src = tmp_path / "image-seed"
    for rel, body in tree.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return [(src, "")]


def test_it_adds_a_file_the_store_does_not_have(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    added = global_seed.top_up(root, _seed(tmp_path, {"README.md": "map"}))
    assert added == ["README.md"]
    assert (root / "README.md").read_text() == "map"


def test_it_never_overwrites_a_file_the_admin_has_edited(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    (root / "README.md").write_text("# Acme Bank\n\nwe hold money.\n")
    added = global_seed.top_up(root, _seed(tmp_path, {"README.md": "# Company\n"}))
    assert added == []
    assert "Acme Bank" in (root / "README.md").read_text(), (
        "a deploy overwrote the company layer — an admin edit changes how every agent in the "
        "deployment behaves and a top-up must never be able to revert one")


def test_it_reaches_into_subdirectories(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    added = global_seed.top_up(root, _seed(tmp_path, {
        "POLICIES.md": "rules", "flows/post_meeting.md": "the page"}))
    assert added == ["POLICIES.md", "flows/post_meeting.md"]
    assert (root / "flows" / "post_meeting.md").read_text() == "the page"


def test_a_dot_directory_in_the_source_is_not_seed_content(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    added = global_seed.top_up(root, _seed(tmp_path, {
        "README.md": "map", ".git/config": "[core]"}))
    assert added == ["README.md"], "a checkout used as a seed source must not carry its own .git in"


def test_the_mail_half_lands_under_mail(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    src = tmp_path / "image-mail"
    src.mkdir()
    (src / "attendee-head.md").write_text("subject: hi\n---\nbody")
    added = global_seed.top_up(root, [(src, global_seed.MAIL_DIRNAME)])
    assert added == ["mail/attendee-head.md"]
    assert (root / "mail" / "attendee-head.md").is_file()


def test_it_is_idempotent(tmp_path):
    root = tmp_path / "_global"
    root.mkdir()
    srcs = _seed(tmp_path, {"README.md": "map", "flows/x.md": "p"})
    global_seed.top_up(root, srcs)
    assert global_seed.top_up(root, srcs) == []


def test_a_read_only_global_is_reported_not_raised(tmp_path):
    """A `_global` bound `:ro` is a legitimate deployment shape and must not stop the boot."""
    root = tmp_path / "_global"
    root.mkdir()
    root.chmod(0o500)
    try:
        assert global_seed.top_up(root, _seed(tmp_path, {"README.md": "map"})) == []
    finally:
        root.chmod(0o700)


def test_a_build_with_no_seed_copies_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(global_seed.ENV_IMAGE_SEED_DIR, str(tmp_path / "nope"))
    monkeypatch.setenv(global_seed.ENV_IMAGE_MAIL_DIR, str(tmp_path / "nope"))
    assert global_seed.image_seed_dir() is None
    assert global_seed.sources() == []
    assert global_seed.top_up(tmp_path) == []


def test_the_summary_names_what_it_added():
    assert "POLICIES.md" in global_seed.summary(["POLICIES.md"])
    assert "nothing to add" in global_seed.summary([])


def test_the_boot_call_site_prints_the_summary_rather_than_logging_it():
    """`agent_api.*` INFO records go nowhere in this service — see `global_seed.summary`."""
    api = (REPO / "core" / "agent" / "control_plane" / "api.py").read_text(encoding="utf-8")
    assert "print(global_seed.summary(" in api


# ── the seed does not lift the gate ──────────────────────────────────────────────────────────────

def _seeded(tmp_path) -> pathlib.Path:
    root = tmp_path / "_global"
    root.mkdir()
    global_seed.top_up(root, [(SEED_IN_REPO, "")])
    return root


def test_the_seeded_layer_is_not_ready(tmp_path):
    st = global_layer.state(_seeded(tmp_path))
    assert not st["ready"], (
        "a scaffolded _global reported itself ready — the instance would open for everyone on five "
        "files nobody has written")
    assert st["company"] is None


def test_the_seeded_layer_says_WHICH_files_are_still_the_placeholder(tmp_path):
    st = global_layer.state(_seeded(tmp_path))
    assert set(st["unwritten"]) == set(global_layer.LAYER_FILES), (
        "every seeded layer file must carry the unwritten marker, or a placeholder passes as "
        "written the moment the gate stops testing emptiness")
    assert st["missing_files"] == list(st["unwritten"])
    assert any("placeholder" in r for r in st["reasons"])


def test_writing_a_file_means_deleting_the_marker(tmp_path):
    root = _seeded(tmp_path)
    (root / "PRINCIPLES.md").write_text("# Principles\n\nWe answer within a day.\n")
    st = global_layer.state(root)
    assert "PRINCIPLES.md" in st["present"]
    assert "PRINCIPLES.md" not in st["unwritten"]


def test_a_fully_written_layer_is_ready(tmp_path):
    root = _seeded(tmp_path)
    (root / "README.md").write_text("# Acme Bank\n\nWe hold money for people.\n")
    for name in ("PRINCIPLES.md", "OBJECTIVES.md", "STRUCTURE.md", "MISSING.md"):
        (root / name).write_text(f"# {name}\n\nsomething true.\n")
    st = global_layer.state(root)
    assert st["ready"], st["reasons"]
    assert st["company"] == "Acme Bank"


def test_an_absent_file_is_still_missing_not_unwritten(tmp_path):
    """The older shape still holds: a `_global` with no seed reports the files as missing."""
    root = tmp_path / "_global"
    root.mkdir()
    st = global_layer.state(root)
    assert set(st["missing_files"]) == set(global_layer.LAYER_FILES)
    assert st["unwritten"] == []
