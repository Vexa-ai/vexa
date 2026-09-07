"""An invite is BOUND TO AN ADDRESS, and the argument list is what enforces it (Vexa-ai/vexa#1635).

The founder said *share it with Marvin*; the agent called `workspace_invite` with no address, got a
link "for anyone who redeems it", and handed it over. That was possible because `emails` defaulted
to `""` — the verb could mint a key to a customer workspace without anybody naming who it was for,
and nothing downstream could tell that link from an intended one.

The address is a required argument now (the signature landed with Vexa-ai/vexa#1632), which is a
stronger guarantee than a docstring: the tool schema the agent reads carries it, so "for anyone who
redeems it" is not a state this verb can reach. That is the one property here.

The verb's gate, its two delivery paths, its refusals and the fact that it composes no link of its
own are in `test_rig_membership.py`, which owns them.
"""
from __future__ import annotations

import inspect

import vexa_control_mcp as rig


def test_the_verb_cannot_be_called_without_an_address():
    sig = inspect.signature(rig.mcp._tool_manager._tools["workspace_invite"].fn)
    assert "email" in sig.parameters
    assert sig.parameters["email"].default is inspect.Parameter.empty, \
        "an address with a default is an invite for nobody in particular"
    # and there is no way back to the old unbound shape
    assert "emails" not in sig.parameters
    assert "open" not in sig.parameters
