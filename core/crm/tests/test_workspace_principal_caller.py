"""D2 (0.13.4): crm-api reads a WORKSPACE principal (a room's turn; `user_id` is its own numeric
account) as shared and never admin — the minimal crm half of D2.
Field filtering for shared callers is D4 (S1-C); this only fixes the caller's kind."""
from __future__ import annotations

from crm.caller import from_validate


def _answer(**dlg_extra):
    return {"user_id": 42, "email": "owner@acme.test", "is_admin": True, "person_is_admin": True,
            "workspaces": ["team"],
            "delegation": {"regime": "human", "workspaces": ["team"], **dlg_extra}}


def test_a_workspace_principal_is_shared_and_never_admin():
    c = from_validate("vxd_x.y.z", _answer(shared=True, principal_kind="workspace", principal_workspace="team", on_behalf_of="43", bills_to=7))
    assert c.shared is True
    assert c.is_admin is False, "the payer's admin bit is not the room's"


def test_a_workspace_principal_is_shared_even_if_shared_went_missing():
    c = from_validate("vxd_x.y.z", _answer(principal_kind="workspace", principal_workspace="team", on_behalf_of="43", bills_to=7))
    assert c.shared is True and c.is_admin is False


def test_a_persons_worker_is_unchanged():
    c = from_validate("vxd_x.y.z", _answer())
    assert c.shared is False and c.is_admin is True
