"""THE THREE SEAMS A PRIVATE FLOW PACK PLUGS INTO — and which of them had to be built.

A deployment that carries flows this repo does not publish needs three things, and only one of them
was missing. These tests state all three, because the two that already worked are now something a
pack may RELY on rather than rediscover, and the property that makes each one safe is easy to
delete by accident.

  1. FLOW VERSIONS AS DATA — `POST /flows` / `Registry.flow_by_names`. Existed. Closed to code: a
     step name outside the image's reviewed vocabulary is refused, which is exactly why (3) exists.
  2. QUEUE WORDS FROM OUTSIDE THE IMAGE — `$VEXA_BEHAVIOR_DIR/queue/<flow>.<type>.md`, read before
     the baked showcase. Existed; asserted in `test_onboarding_flow.py`.
  3. STEPS OF ITS OWN — `VEXA_FLOWS_DEFS_EXTRA`. THE ONE THAT WAS MISSING.

And the fourth thing a pack needs, which is an absence rather than a seam: event types it invents
are admissible the moment a flow reacts to them. There is no carrier allow-list on the intake.
"""
from __future__ import annotations

import sys
import textwrap

import pytest

from flows import Done, EventType, Registry
from flows_defs import production


PACK = textwrap.dedent('''
    """A flow pack, exactly as a private deployment would ship one."""
    from flows import Done, EventType

    PRIVATE = EventType("private.thing.happened")

    def build(reg, db):
        @reg.step
        def private_step(ctx):
            """A step this repo has never heard of."""
            return Done({"private": True})
        reg.flow(name="private_flow", version=1, on=PRIVATE, steps=[reg.steps["private_step"]])
''')


@pytest.fixture
def pack_on_path(tmp_path, monkeypatch):
    (tmp_path / "acme_pack.py").write_text(PACK, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "acme_pack"
    sys.modules.pop("acme_pack", None)


# ── 3. steps of its own ──────────────────────────────────────────────────────────────────────────
def test_no_pack_named_registers_only_this_repos_flows(registry):
    assert "private_flow" not in {n for n, _v in registry.flows}
    assert "private_step" not in registry.steps


def test_a_named_pack_adds_its_steps_and_flows(db, monkeypatch, pack_on_path):
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, pack_on_path)
    reg = Registry()
    production.build(reg, db)
    assert reg.flows[("private_flow", 1)].on.name == "private.thing.happened"
    assert reg.steps["private_step"](None).result == {"private": True}


def test_a_pack_composes_on_top_and_replaces_nothing(db, monkeypatch, pack_on_path):
    """It runs LAST, and it cannot edit what is above it: `Registry.step` refuses a name already
    bound to a different function, and a flow is superseded only by a higher version."""
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, pack_on_path)
    reg = Registry()
    production.build(reg, db)
    for kept in ("onboarding", "post_meeting", "live_meeting", "invite_intake", "friction_log"):
        assert kept in {n for n, _v in reg.flows}


def test_several_packs_are_comma_separated(db, tmp_path, monkeypatch):
    for name in ("pack_a", "pack_b"):
        (tmp_path / f"{name}.py").write_text(
            PACK.replace("private_step", f"{name}_step")
                .replace("private_flow", f"{name}_flow")
                .replace("private.thing.happened", f"{name}.happened"), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, " pack_a , pack_b ")
    reg = Registry()
    try:
        production.build(reg, db)
        assert {"pack_a_flow", "pack_b_flow"} <= {n for n, _v in reg.flows}
    finally:
        for name in ("pack_a", "pack_b"):
            sys.modules.pop(name, None)


def test_a_named_pack_that_cannot_be_imported_refuses_to_boot(db, monkeypatch):
    """THE WHOLE CARE IN THIS SEAM. Every other absence in `production.py` is tolerated because
    absence is a supported shape. Here the deployment has SAID it has a pack — a quiet failure
    would leave flows booting, serving, admitting facts, and reacting to none of the pack's events
    with nothing anywhere saying so."""
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, "no_such_pack_anywhere")
    with pytest.raises(ImportError) as e:
        production.build(Registry(), db)
    assert production.DEFS_EXTRA_ENV in str(e.value)
    assert "no_such_pack_anywhere" in str(e.value)


def test_a_module_with_no_build_is_refused_by_name(db, tmp_path, monkeypatch):
    (tmp_path / "shapeless_pack.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, "shapeless_pack")
    try:
        with pytest.raises(TypeError) as e:
            production.build(Registry(), db)
        assert "build(reg, db)" in str(e.value)
    finally:
        sys.modules.pop("shapeless_pack", None)


# ── 1. flow versions as data ─────────────────────────────────────────────────────────────────────
def test_a_flow_version_may_be_authored_from_existing_steps(registry):
    """EXTENSION POINT (1/3), which already existed: a deployment reorders the reviewed vocabulary
    into a flow of its own without an image rebuild (`POST /flows` writes the row, the worker
    hot-loads it within one refresh)."""
    f = registry.flow_by_names(name="tenant_flow", version=9, on_event="tenant.event",
                               step_names=["first_meeting"])
    assert f.steps == ("first_meeting",)
    assert ("tenant_flow", 9) in registry.db_versions


def test_authoring_never_accepts_a_step_the_image_does_not_have(registry):
    """…and why (3) has to exist: the API is closed to code, on purpose."""
    with pytest.raises(ValueError) as e:
        registry.flow_by_names(name="tenant_flow", version=9, on_event="tenant.event",
                               step_names=["charge_the_card"])
    assert "charge_the_card" in str(e.value)


# ── 4. event types a pack invents ────────────────────────────────────────────────────────────────
def test_admission_has_no_carrier_allow_list(db, clock, monkeypatch, pack_on_path):
    """EXTENSION POINT (3/3 of the founder's questions): a private flow's own event types need no
    entry anywhere in this repo. Admission matches on the REGISTRY — register a flow on a type and
    that type is admissible the same tick; register none and nothing reacts, which is the only
    thing the intake refuses."""
    from flows import admit
    monkeypatch.setenv(production.DEFS_EXTRA_ENV, pack_on_path)
    reg = Registry()
    production.build(reg, db)
    n = admit(db, reg, clock, source_event_id="p-1", event_type="private.thing.happened",
              subject_refs={"uid": "77"})
    assert n == 1
    assert admit(db, reg, clock, source_event_id="p-2", event_type="nobody.reacts.to.this",
                 subject_refs={"uid": "77"}) == 0
