"""A CATALOG ENTRY CAN BE OPEN TO NAMED PEOPLE ONLY — `allowed_subjects`.

An operator who offers a model on a credential that belongs to one person (a personal subscription)
must be able to say so: the entry lists the addresses (or subject ids) that may pick it. Everyone
else neither sees it in the picker nor can run it by naming it — a forced pick is the typed
`not_permitted` refusal, before anything is spawned. Unknown identity fails closed.

L1: pure catalog, no app.
"""
from __future__ import annotations

import copy
import json

import pytest

from control_plane.model_providers import CatalogError, ModelChoiceFault, parse
from control_plane.model_providers.port import NOT_PERMITTED, RouteContext
from tests.model_catalogs import ENV, EXAMPLE

OWNER = ["owner@example.test", "owner+alt@example.test"]


def _catalog(allowed=OWNER):
    decl = copy.deepcopy(EXAMPLE)
    decl["models"][2]["allowed_subjects"] = allowed     # the subscription entry, "claude"
    return parse(json.dumps(decl), ENV)


def _as(*ids):
    return lambda: list(ids)


CTX = RouteContext()


def test_the_named_person_sees_and_runs_the_entry():
    cat = _catalog()
    ids = [m["id"] for m in cat.listing(CTX, admin=False, person=_as("u1", "Owner@Example.test"))["models"]]
    assert "claude" in ids
    assert cat.route("claude", CTX, admin=False, person=_as("u1", "owner@example.test")).model_id == "claude"


def test_a_second_listed_address_also_qualifies():
    assert _catalog().choose("claude", CTX, admin=False, person=_as("u9", "owner+alt@example.test"))["id"] == "claude"


def test_a_subject_id_may_be_listed():
    cat = _catalog(["u42"])
    assert cat.choose("claude", CTX, admin=False, person=_as("u42", "x@example.test"))["id"] == "claude"


def test_anyone_else_does_not_see_it():
    ids = [m["id"] for m in _catalog().listing(CTX, admin=False, person=_as("u2", "other@example.test"))["models"]]
    assert "claude" not in ids and "qwen3-32b" in ids


def test_anyone_else_is_refused_a_forced_pick():
    with pytest.raises(ModelChoiceFault) as exc:
        _catalog().route("claude", CTX, admin=False, person=_as("u2", "other@example.test"))
    assert exc.value.kind == NOT_PERMITTED
    assert "owner@" not in exc.value.sentence()       # the list is never disclosed


def test_an_admin_is_not_thereby_on_the_list():
    with pytest.raises(ModelChoiceFault):
        _catalog().choose("claude", CTX, admin=True, person=_as("u3", "admin@example.test"))


def test_unknown_identity_fails_closed():
    cat = _catalog()
    with pytest.raises(ModelChoiceFault):
        cat.choose("claude", CTX, admin=False)                       # no person resolver at all
    with pytest.raises(ModelChoiceFault):
        cat.choose("claude", CTX, admin=False, person=_as())         # identity could not answer


def test_a_restricted_default_falls_through_for_others():
    decl = copy.deepcopy(EXAMPLE)
    for m in decl["models"]:
        m.pop("default", None)
    decl["models"][2].update(default=True, allowed_subjects=OWNER)
    cat = parse(json.dumps(decl), ENV)
    assert cat.default_for(CTX, admin=False, person=_as("u1", "owner@example.test")) == "claude"
    assert cat.default_for(CTX, admin=False, person=_as("u2", "other@example.test")) == "qwen3-32b"


def test_entries_without_the_field_are_unchanged():
    cat = _catalog()
    assert cat.choose("qwen3-32b", CTX, admin=False)["id"] == "qwen3-32b"


@pytest.mark.parametrize("bad", [[], [""], "owner@example.test", [1]])
def test_a_malformed_list_is_refused_at_boot(bad):
    decl = copy.deepcopy(EXAMPLE)
    decl["models"][2]["allowed_subjects"] = bad
    with pytest.raises(CatalogError) as exc:
        parse(json.dumps(decl), ENV)
    assert any("allowed_subjects" in p for p in exc.value.problems)
