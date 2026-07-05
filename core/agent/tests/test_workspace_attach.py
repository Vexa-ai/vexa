"""workspace_attach — attach a custom external git repo as a subject's workspace, swappable.

Proves the swap lifecycle on REAL git over local repos (no network):
  seed → attach custom repo (parks seed) → swap back to seed (restores park) →
  re-attach the same repo (restores its park, NO re-clone) → idempotent no-op.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_attach import (
    CloneError,
    _authenticated_url,
    _git_clone,
    attached_workspaces,
    rename_workspace,
    swap_workspace,
)


def _template(tmp_path: Path, marker: str = "TEMPLATE ROOT") -> Path:
    """A minimal VALID seed template (carries a governance root) for the reseed/start-fresh path."""
    t = tmp_path / "template"
    t.mkdir()
    (t / "CLAUDE.md").write_text(marker)
    return t


def _make_repo(path: Path, marker: str, *, compliant: bool = True) -> str:
    """A local git repo with a ``MARK`` file carrying ``marker`` — the clone source (no network). By
    default it carries a ``CLAUDE.md`` governance root (a compliant workspace); pass ``compliant=False``
    for a plain repo with no governance root (exercises the nest-under-kg path)."""
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@test")
    run("config", "user.name", "t")
    (path / "MARK").write_text(marker)
    if compliant:
        (path / "CLAUDE.md").write_text("CUSTOM ROOT")
    run("add", "-A")
    run("commit", "-q", "-m", "seed")
    return str(path)


def _seed_active(root: Path, subject: str, marker: str = "SEED") -> Path:
    """Stand up an initial seeded active workspace at <root>/<subject>."""
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text(marker)
    seed_workspace(ws, None)
    return ws


def test_attach_custom_repo_parks_the_seed(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")

    res = swap_workspace(root, "u1", origin, "main")

    assert res.swapped is True and res.cloned is True
    assert res.parked_slug == "seed"
    # active is now the custom repo
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / "CLAUDE.md").read_text() == "CUSTOM ROOT"   # the custom repo's own root
    # the seed was PARKED (kept), never destroyed
    assert (root / ".attached" / "u1" / "seed" / "CLAUDE.md").read_text() == "SEED"
    view = attached_workspaces(root, "u1")
    assert view["active"] == res.active_slug
    assert "seed" in view["slots"]


def test_swap_back_to_seed_restores_the_park(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    res = swap_workspace(root, "u1", None)  # repo=None → swap back to seed

    assert res.active_slug == "seed" and res.swapped is True and res.cloned is False
    assert (root / "u1" / "CLAUDE.md").read_text() == "SEED"   # original seed restored
    # the custom repo is now the parked one
    custom_slug = res.parked_slug
    assert (root / ".attached" / "u1" / custom_slug / "MARK").read_text() == "CUSTOM"


def test_swap_back_to_custom_restores_without_recloning(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    # make a local edit in the attached custom workspace, then swap away and back
    (root / "u1" / "LOCAL").write_text("edit")
    swap_workspace(root, "u1", None)  # → seed

    calls: list = []
    def _no_clone(repo, ref, dest, token=None):  # restore must NOT clone
        calls.append((repo, ref))
    res = swap_workspace(root, "u1", origin, "main", clone=_no_clone)

    assert res.swapped is True and res.cloned is False
    assert calls == []                                   # restored the parked tree, no re-clone
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / "LOCAL").read_text() == "edit"  # detached edits persisted


def test_swap_to_seed_on_never_swapped_workspace_is_noop(tmp_path):
    """Regression (the workspace-reset bug): a freshly-init'd subject has NO attach state (active is None)
    and is already ON the seed. Clicking 'default (seed)' must be a NO-OP — it must NOT park the live
    workspace under the 'seed' slug and swap in a blank re-seed, which orphaned the subject's real data."""
    root = tmp_path / "workspaces"
    ws = _seed_active(root, "u1")
    (ws / "kg").mkdir(exist_ok=True)
    (ws / "kg" / "real-note.md").write_text("MY REAL WORK")          # the subject's actual work, in-place
    assert attached_workspaces(root, "u1")["active"] is None          # never swapped

    res = swap_workspace(root, "u1", None)                            # click "default (seed)"

    assert res.swapped is False and res.parked_slug is None           # no-op, nothing parked
    assert (ws / "kg" / "real-note.md").read_text() == "MY REAL WORK"  # live workspace untouched
    assert not (root / ".attached" / "u1" / "seed").exists()          # no destructive park created


def test_compliant_repo_is_used_as_is(tmp_path):
    """A clone that already carries a governance root (CLAUDE.md) is the workspace as-is — not nested."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT")
    run("add", "-A"); run("commit", "-q", "-m", "x")

    res = swap_workspace(root, "u1", str(origin), "main")

    assert res.nested is False
    assert (root / "u1" / "CLAUDE.md").read_text() == "CUSTOM ROOT"  # used directly


def test_noncompliant_repo_is_nested_under_kg_of_a_template_workspace(tmp_path, monkeypatch):
    """A clone with NO governance root is wrapped: a fresh template workspace is materialized and the
    clone is nested under kg/<name>/ (its own .git dropped, folded into the governed workspace's git)."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "CLAUDE.md").write_text("TEMPLATE ROOT")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(template))

    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "data-repo", "RAW", compliant=False)  # no CLAUDE.md

    res = swap_workspace(root, "u1", str(origin), "main")

    assert res.nested is True and res.cloned is True
    ws = root / "u1"
    assert (ws / "CLAUDE.md").read_text() == "TEMPLATE ROOT"          # governed by the template root
    assert (ws / "kg" / "data-repo" / "MARK").read_text() == "RAW"    # clone nested under kg/
    assert not (ws / "kg" / "data-repo" / ".git").exists()            # nested clone's git was dropped
    # the nested import is committed into the workspace repo (clean tree)
    status = subprocess.run(["git", "-C", str(ws), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout.strip() == ""


def test_requesting_active_repo_is_a_noop(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    res = swap_workspace(root, "u1", origin, "main")  # already active
    assert res.swapped is False and res.cloned is False and res.parked_slug is None


def test_authenticated_url_embeds_token_for_https_only():
    assert _authenticated_url("https://github.com/o/r.git", "TOK") == "https://TOK@github.com/o/r.git"
    assert _authenticated_url("https://github.com/o/r.git", None) == "https://github.com/o/r.git"
    assert _authenticated_url("git@github.com:o/r.git", "TOK") == "git@github.com:o/r.git"  # ssh: untouched
    assert _authenticated_url("/local/path", "TOK") == "/local/path"                         # local: untouched


def test_token_threads_to_clone_but_is_never_stored(tmp_path):
    """A private-repo token reaches the clone fn but is NOT persisted in the attachment state (P15)."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    seen = {}

    def fake_clone(repo, ref, dest, token=None):       # simulate a compliant private clone
        seen["token"] = token
        dest.mkdir(parents=True)
        (dest / "CLAUDE.md").write_text("ROOT")
        (dest / "MARK").write_text("PRIVATE")
        subprocess.run(["git", "init", "-q", str(dest)], check=True, capture_output=True)

    res = swap_workspace(root, "u1", "https://github.com/o/private.git", "main",
                         token="SEKRET-TOKEN", clone=fake_clone)

    assert seen["token"] == "SEKRET-TOKEN" and res.cloned is True
    assert (root / "u1" / "MARK").read_text() == "PRIVATE"
    state_text = (root / ".attached" / "u1" / "state.json").read_text()
    assert "SEKRET-TOKEN" not in state_text                       # token absent from persisted state
    assert "token" not in json.loads(state_text)["slots"][res.active_slug]


def test_clone_error_redacts_token(tmp_path):
    """A failed authenticated clone raises CloneError with the token scrubbed from the message (P15)."""
    with pytest.raises(CloneError) as ei:
        _git_clone("https://invalid.invalid/nope.git", "main", tmp_path / "dest", token="SUPERSECRET")
    assert "SUPERSECRET" not in str(ei.value)


def test_failed_clone_leaves_active_workspace_and_state_intact(tmp_path):
    """REGRESSION: a swap whose clone FAILS must not disturb the live workspace — the active tree stays
    in place, no half-parked state, and a subsequent swap-back still works. (Previously the active
    workspace was parked BEFORE the clone, so a clone error left the user with no workspace.)"""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1", "SEED-ROOT")
    # establish a known-good attached repo first, so there's a real active workspace + state to protect
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    state_before = (root / ".attached" / "u1" / "state.json").read_text()

    def boom(repo, ref, dest, token=None):
        raise CloneError("remote: Repository not found")

    with pytest.raises(CloneError):
        swap_workspace(root, "u1", "https://github.com/x/private.git", "main", clone=boom)

    # the live workspace is UNTOUCHED — still the custom repo, still a real repo
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / ".git").exists()
    # state is unchanged (no phantom park of the active workspace)
    assert (root / ".attached" / "u1" / "state.json").read_text() == state_before
    # and a legitimate swap-back to seed still works afterwards
    res = swap_workspace(root, "u1", None)
    assert res.active_slug == "seed" and (root / "u1" / "CLAUDE.md").read_text() == "SEED-ROOT"


def test_invalid_subject_rejected(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    with pytest.raises(ValueError):
        swap_workspace(root, "../escape", "x")
    with pytest.raises(ValueError):
        swap_workspace(root, ".attached", "x")  # reserved store namespace


# ── start fresh (rebuild the default from the template) ───────────────────────────────────────────────
def test_start_fresh_reseeds_the_default_and_keeps_a_recoverable_backup(tmp_path, monkeypatch):
    """'start fresh' on a never-swapped workspace rebuilds the default from the template. The live tree
    (the subject's real work) is NOT destroyed — it is tucked under the recoverable backup slot."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    ws = _seed_active(root, "u1", "OLD-DEFAULT")
    (ws / "kg").mkdir(exist_ok=True)
    (ws / "kg" / "real.md").write_text("MY REAL WORK")
    assert attached_workspaces(root, "u1")["active"] is None          # never swapped

    res = swap_workspace(root, "u1", None, fresh=True)                # 'start fresh'

    assert res.swapped is True and res.active_slug == "seed"
    assert (ws / "CLAUDE.md").read_text() == "TEMPLATE ROOT"          # default rebuilt from the template
    assert not (ws / "kg" / "real.md").exists()                       # blank — none of the old work
    backup = root / ".attached" / "u1" / "seed-prev"
    assert (backup / "kg" / "real.md").read_text() == "MY REAL WORK"  # old default kept, recoverable
    view = attached_workspaces(root, "u1")
    assert view["active"] == "seed" and view["slots"]["seed-prev"]["name"] == "default (previous)"


def test_start_fresh_from_an_attached_repo_resets_the_default_and_parks_the_repo(tmp_path, monkeypatch):
    """Starting fresh while on an attached repo: the repo is parked (recoverable), the previously-parked
    default is moved to the backup, and the active default becomes a blank template."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    _seed_active(root, "u1", "OLD-DEFAULT")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")                        # attach repo → old default parked
    repo_slug = attached_workspaces(root, "u1")["active"]

    res = swap_workspace(root, "u1", None, fresh=True)

    assert res.active_slug == "seed"
    assert (root / "u1" / "CLAUDE.md").read_text() == "TEMPLATE ROOT"             # blank template default
    assert (root / ".attached" / "u1" / repo_slug / "MARK").read_text() == "CUSTOM"     # repo parked
    assert (root / ".attached" / "u1" / "seed-prev" / "CLAUDE.md").read_text() == "OLD-DEFAULT"


def test_start_fresh_backup_is_recoverable_via_slug(tmp_path, monkeypatch):
    """The start-fresh backup is a normal parked slot — swapping to it BY SLUG restores the old default."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    ws = _seed_active(root, "u1", "OLD-DEFAULT")
    (ws / "kg").mkdir(exist_ok=True)
    (ws / "kg" / "real.md").write_text("MY REAL WORK")
    swap_workspace(root, "u1", None, fresh=True)                      # backup at seed-prev, active=blank

    res = swap_workspace(root, "u1", None, slug="seed-prev")          # swap back to the backup by slug

    assert res.swapped is True and res.active_slug == "seed-prev"
    assert (ws / "kg" / "real.md").read_text() == "MY REAL WORK"      # old default restored intact


def test_start_fresh_carries_the_seed_name_onto_the_backup(tmp_path, monkeypatch):
    """A renamed default's label persists across the rebuild and tags the backup, so it stays identifiable."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    ws = _seed_active(root, "u1")
    (ws / "kg").mkdir(exist_ok=True)
    (ws / "kg" / "real.md").write_text("WORK")
    rename_workspace(root, "u1", "seed", "Workbench")

    swap_workspace(root, "u1", None, fresh=True)

    view = attached_workspaces(root, "u1")
    assert view["slots"]["seed"]["name"] == "Workbench"              # label persists onto the fresh default
    assert view["slots"]["seed-prev"]["name"] == "Workbench (previous)"


# ── swap by slug · rename ─────────────────────────────────────────────────────────────────────────────
def test_swap_by_slug_restores_a_parked_repo_without_recloning(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    slug = swap_workspace(root, "u1", origin, "main").active_slug
    swap_workspace(root, "u1", None)                                 # → seed (repo now parked)

    calls: list = []
    back = swap_workspace(root, "u1", None, slug=slug, clone=lambda *a, **k: calls.append(a))

    assert back.active_slug == slug and back.cloned is False and calls == []
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"


def test_swap_unknown_slug_with_no_repo_raises(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    with pytest.raises(KeyError):
        swap_workspace(root, "u1", None, slug="never-parked")        # nothing to restore, nothing to clone


def test_rename_sets_a_display_name_without_touching_the_slug(tmp_path):
    """Rename is a label only: the slug + repo are unchanged, so swap-back / repo re-attach keep matching."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    slug = swap_workspace(root, "u1", origin, "main").active_slug

    view = rename_workspace(root, "u1", slug, "My Project")
    assert view["slots"][slug]["name"] == "My Project"
    assert view["slots"][slug]["repo"] == origin                     # slug/repo intact
    assert attached_workspaces(root, "u1")["slots"][slug]["name"] == "My Project"  # persisted

    swap_workspace(root, "u1", None)                                 # → seed
    calls: list = []
    again = swap_workspace(root, "u1", origin, "main", clone=lambda *a, **k: calls.append(a))
    assert again.cloned is False and calls == [] and again.active_slug == slug   # rename didn't break matching


def test_rename_clears_label_and_rejects_unknown_slug(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    rename_workspace(root, "u1", "seed", "Home")                     # the reserved seed can be named
    assert attached_workspaces(root, "u1")["slots"]["seed"]["name"] == "Home"
    rename_workspace(root, "u1", "seed", "")                         # empty clears it
    assert "name" not in attached_workspaces(root, "u1")["slots"]["seed"]
    with pytest.raises(KeyError):
        rename_workspace(root, "u1", "does-not-exist", "X")


# ── the additive mount set (WP-A2.1): activate ADDS without parking, deactivate parks, seed always active ──

from control_plane.workspace_attach import (  # noqa: E402
    activate_workspace,
    active_workspaces,
    deactivate_workspace,
)


def test_activate_adds_to_the_set_without_parking_the_others(tmp_path):
    """activate is ADDITIVE: the private baseline stays in place (still at <root>/<subject>, still active)
    and the new workspace joins the set — the swap-park behavior must NOT fire."""
    root = tmp_path / "workspaces"
    seed_ws = _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "SHARED")

    res = activate_workspace(root, "u1", origin, "main")

    assert res.changed is True and res.cloned is True
    # the private baseline is UNTOUCHED — still the live tree at <root>/<subject> (NOT parked)
    assert (seed_ws / "CLAUDE.md").read_text() == "SEED"
    assert not (root / ".attached" / "u1" / "seed").exists()          # seed was NOT parked
    # the new workspace materialized in its store slot and joined the set
    mounts = active_workspaces(root, "u1")
    slugs = [m.slug for m in mounts]
    assert slugs[0] == "seed" and res.slug in slugs                   # private first, new one present
    added = next(m for m in mounts if m.slug == res.slug)
    assert added.repo == origin and added.role == "private" and added.primary is False
    assert Path(added.path) == root / ".attached" / "u1" / res.slug   # secondary lives in its slot


def test_activate_is_idempotent_and_the_private_baseline_needs_no_activation(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "SHARED")
    slug = activate_workspace(root, "u1", origin, "main").slug

    # re-activating the same repo → no-op (no re-clone), and activating the seed is a no-op too
    calls: list = []
    again = activate_workspace(root, "u1", origin, "main", clone=lambda *a, **k: calls.append(a))
    assert again.changed is False and calls == []
    assert activate_workspace(root, "u1", None, slug="seed").changed is False
    # the set is unchanged: exactly {seed, slug}
    assert [m.slug for m in active_workspaces(root, "u1")] == ["seed", slug]


def test_deactivate_parks_the_secondary_without_destroying_it(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "SHARED")
    slug = activate_workspace(root, "u1", origin, "main").slug
    slot_dir = root / ".attached" / "u1" / slug
    assert slot_dir.exists()

    res = deactivate_workspace(root, "u1", slug)

    assert res.changed is True
    assert [m.slug for m in active_workspaces(root, "u1")] == ["seed"]  # dropped from the set
    assert slot_dir.exists()                                            # tree KEPT (parked), never destroyed
    # deactivate is idempotent, and re-activating restores WITHOUT a re-clone (the slot is already there)
    assert deactivate_workspace(root, "u1", slug).changed is False
    calls: list = []
    again = activate_workspace(root, "u1", None, slug=slug, clone=lambda *a, **k: calls.append(a))
    assert again.changed is True and calls == []
    assert slug in [m.slug for m in active_workspaces(root, "u1")]


def test_seed_is_always_active_and_cannot_be_deactivated(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    # never-swapped subject: the set is exactly the private baseline (the seed), marked primary
    mounts = active_workspaces(root, "u1")
    assert len(mounts) == 1 and mounts[0].slug == "seed" and mounts[0].primary is True
    assert mounts[0].write is True and Path(mounts[0].path) == root / "u1"
    with pytest.raises(ValueError):
        deactivate_workspace(root, "u1", "seed")


def test_active_set_survives_a_swap_of_the_primary(tmp_path):
    """A swap only re-homes the PRIVATE baseline; secondary active members must survive it."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    shared = _make_repo(tmp_path / "shared", "SHARED")
    other = _make_repo(tmp_path / "other", "OTHER")
    shared_slug = activate_workspace(root, "u1", shared, "main").slug

    # swap the PRIMARY to `other` — the secondary `shared` stays active
    swap_workspace(root, "u1", other, "main")
    slugs = [m.slug for m in active_workspaces(root, "u1")]
    assert slugs[0] != "seed"                                          # primary is now `other`
    assert shared_slug in slugs                                       # the secondary survived the swap
    assert next(m for m in active_workspaces(root, "u1") if m.slug == slugs[0]).primary is True


def test_active_set_back_compat_when_state_has_no_active_set_field(tmp_path):
    """A state.json written before active_set existed → the set is just the single active workspace."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    slug = swap_workspace(root, "u1", origin, "main").active_slug     # legacy single-active swap
    # simulate a legacy state.json: drop the active_set field
    import json as _json
    sf = root / ".attached" / "u1" / "state.json"
    data = _json.loads(sf.read_text()); data.pop("active_set", None); sf.write_text(_json.dumps(data))

    mounts = active_workspaces(root, "u1")
    assert [m.slug for m in mounts] == [slug] and mounts[0].primary is True


# ── create a brand-new BLANK workspace (additive "new workspace" — replaces list-level 'start fresh') ──

from control_plane.workspace_attach import create_workspace  # noqa: E402


def test_create_new_seeds_a_fresh_workspace_and_adds_it_without_touching_the_baseline(tmp_path, monkeypatch):
    """'New workspace' CREATES a fresh template-seeded workspace at a NEW slug and ADDS it to the active
    set — the baseline (and any other active workspace) is NOT parked, rebuilt, or backed up."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    seed_ws = _seed_active(root, "u1", "MY-BASELINE")
    (seed_ws / "kg").mkdir(exist_ok=True)
    (seed_ws / "kg" / "real.md").write_text("MY REAL WORK")

    res = create_workspace(root, "u1")

    assert res.changed is True and res.cloned is False
    assert res.slug == "workspace-1"                                    # fresh unique slug
    # the baseline is UNTOUCHED — still the live tree at <root>/<subject>, its work intact, NOT parked
    assert (seed_ws / "CLAUDE.md").read_text() == "MY-BASELINE"
    assert (seed_ws / "kg" / "real.md").read_text() == "MY REAL WORK"
    assert not (root / ".attached" / "u1" / "seed").exists()           # baseline NOT parked
    assert not (root / ".attached" / "u1" / "seed-prev").exists()      # nothing backed up
    # the new workspace was materialized (seeded from the template + git-inited) in its store slot
    slot = root / ".attached" / "u1" / "workspace-1"
    assert (slot / "CLAUDE.md").read_text() == "TEMPLATE ROOT"
    assert (slot / ".git").exists()
    # …and JOINED the active set (checked/active), baseline stays primary + first
    mounts = active_workspaces(root, "u1")
    slugs = [m.slug for m in mounts]
    assert slugs[0] == "seed" and mounts[0].primary is True            # baseline still primary
    added = next(m for m in mounts if m.slug == "workspace-1")
    assert added.repo is None and added.role == "private" and added.primary is False
    assert Path(added.path) == slot
    assert attached_workspaces(root, "u1")["slots"]["workspace-1"]["name"] == "New workspace"


def test_create_new_mints_unique_slugs_and_names(tmp_path, monkeypatch):
    """Each create gets a distinct slug (workspace-1, -2, …) and a soft-unique default name."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")

    a = create_workspace(root, "u1")
    b = create_workspace(root, "u1")

    assert {a.slug, b.slug} == {"workspace-1", "workspace-2"}
    view = attached_workspaces(root, "u1")
    names = {view["slots"]["workspace-1"]["name"], view["slots"]["workspace-2"]["name"]}
    assert names == {"New workspace", "New workspace 2"}
    # both joined the set alongside the untouched baseline
    assert [m.slug for m in active_workspaces(root, "u1")] == ["seed", "workspace-1", "workspace-2"]


def test_create_new_honors_a_given_name(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")

    res = create_workspace(root, "u1", name="Research")

    assert attached_workspaces(root, "u1")["slots"][res.slug]["name"] == "Research"


def test_create_new_does_not_park_or_disturb_an_existing_active_secondary(tmp_path, monkeypatch):
    """Creating a new workspace leaves ALREADY-active secondaries (activated repos) mounted + in place."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    shared = _make_repo(tmp_path / "shared", "SHARED")
    shared_slug = activate_workspace(root, "u1", shared, "main").slug

    res = create_workspace(root, "u1")

    slugs = [m.slug for m in active_workspaces(root, "u1")]
    assert slugs == ["seed", shared_slug, res.slug]                    # both survive; new one appended
    # the pre-existing secondary's tree is untouched
    assert (root / ".attached" / "u1" / shared_slug / "MARK").read_text() == "SHARED"


def test_create_new_slug_skips_a_taken_workspace_n_slug(tmp_path, monkeypatch):
    """Slug minting avoids colliding with an existing slot that happens to already use a workspace-N name."""
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_template(tmp_path)))
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    # pre-seed the state with a slot already named workspace-1 (e.g. a parked repo carrying that slug)
    store = root / ".attached" / "u1"
    store.mkdir(parents=True, exist_ok=True)
    import json as _json
    (store / "state.json").write_text(_json.dumps(
        {"active": None, "slots": {"workspace-1": {"repo": "x", "ref": "main"}}, "active_set": []}))

    res = create_workspace(root, "u1")
    assert res.slug == "workspace-2"                                    # skipped the taken workspace-1
