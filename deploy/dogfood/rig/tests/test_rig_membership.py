"""The two membership verbs — `workspace_invite` and `workspace_membership` (Vexa-ai/vexa#1632).

Founder, 2026-09-06, pressing **Add a member…** on a workspace front page and reading
`invite role must be one of ('contributor',)` back: *"this add member should just ask chat to do
that with mcp, asking their emails etc."* and *"so we do not have to create UI here — button to
trigger the chat."* The page has no membership form any more, so these two verbs are the ONLY way
somebody is added, re-roled or removed. That is what makes each of the things asserted here
load-bearing rather than tidy:

* both go through **agent-api's own routes on the caller's identity**, where the owner check, the
  `_system` refusal and the `_global` rule live — the same F96 rule `workspace_write` took;
* **the address is the argument**, not a subject id, because the person asking says a name out loud;
* **the link is composed by the server**, which is the defect this replaces: the old verb built
  `<canonical>/join?i=<token>` out of the MCP endpoint's own base, and this product has never served
  a `/join` path from that host. Every invite link it handed out went nowhere, and nothing could see
  it because nothing composed and resolved a link in the same place;
* **a refusal is told plainly and never routed around** — there is exactly one other invite route in
  this rig's reach (`POST /api/workspace/invites`), it does not run the owner gate, and a verb that
  fell back to it on a 403 would turn a refusal into a grant.
"""
from __future__ import annotations

import ast
import json
import pathlib

from conftest import as_user, tool

RIG = pathlib.Path(__file__).resolve().parents[1] / "vexa_control_mcp.py"


def _src() -> str:
    return RIG.read_text()


def _node(name: str):
    for node in ast.parse(_src()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from the rig — re-read it before trusting this test")


def _fn(name: str) -> str:
    """The CODE of one verb, docstring excluded — the same reader `test_rig_workspace_write` uses,
    and for its stated reason: these bodies explain in prose what they no longer do, and a scan that
    cannot tell an explanation from an instruction would force the history out of the file."""
    src, node = _src(), _node(name)
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.get_source_segment(src, n) or "" for n in body)


def _doc(name: str) -> str:
    """A verb's docstring — the whole of what a model reads before deciding how to use it."""
    return ast.get_docstring(_node(name)) or ""


def _tool_names() -> set:
    import vexa_control_mcp as rig
    return set(rig.mcp._tool_manager._tools)

WS = "pilot"
THEM = "jsmith@example.com"

MAILED = {"workspace": WS, "email": THEM, "role": "contributor",
          "role_sentence": "a contributor writes this group",
          "invited": True, "already_member": False, "internal": False, "delivery": "mailed",
          "link": "https://app.example.test/?invite=tok", "invite_id": "abc", "expires_at": 1,
          "said": "Invited jsmith@example.com to pilot as a contributor."}

HANDED = dict(MAILED, internal=True, delivery="link")

REMOVED = {"workspace": WS, "email": THEM, "subject": "u_them", "removed": True, "role": None,
           "said": "Removed jsmith@example.com from pilot."}


def _invite(monkeypatch, routes=None, **kw):
    http = as_user(monkeypatch, uid="7", routes=routes)
    return http, json.loads(tool("workspace_invite")(**kw))


def _membership(monkeypatch, routes=None, **kw):
    http = as_user(monkeypatch, uid="7", routes=routes)
    return http, json.loads(tool("workspace_membership")(**kw))


# ── the door ─────────────────────────────────────────────────────────────────────────────────────
def test_an_invite_goes_through_agent_api_on_the_callers_identity(monkeypatch):
    http, out = _invite(monkeypatch, routes={"/api/workspace/invite": (200, MAILED)},
                        slug=WS, email=THEM, role="contributor")
    call = next(c for c in http.calls if "/api/workspace/invite" in c["url"])
    assert call["method"] == "POST"
    assert call["headers"]["X-User-Id"] == "7"
    assert call["body"] == {"slug": WS, "email": THEM, "role": "contributor"}
    assert out["delivery"] == "mailed"


def test_a_membership_change_goes_through_the_same_door(monkeypatch):
    http, out = _membership(monkeypatch, routes={"/api/workspace/membership": (200, REMOVED)},
                            slug=WS, email=THEM, role="remove")
    call = next(c for c in http.calls if "/api/workspace/membership" in c["url"])
    assert call["method"] == "POST"
    assert call["headers"]["X-User-Id"] == "7"
    assert call["body"] == {"slug": WS, "email": THEM, "role": "remove"}
    assert out["removed"] is True


def test_the_default_role_is_the_smallest_one(monkeypatch):
    """`reader` — a reader reads this group and does not write it. A default that wrote would be a
    permission granted by an omitted argument."""
    http, _out = _invite(monkeypatch, routes={"/api/workspace/invite": (200, MAILED)},
                         slug=WS, email=THEM)
    assert next(c for c in http.calls if "/api/workspace/invite" in c["url"])["body"]["role"] == "reader"


# ── the link ─────────────────────────────────────────────────────────────────────────────────────
def test_the_verb_composes_no_link_of_its_own(monkeypatch):
    """THE DEFECT THIS REPLACES. The old body built `f"{base}/join?i={tok}"` from `CANONICAL` — the
    MCP endpoint — and that path has never been served. The link now comes from the route, which
    reads `VEXA_UI_URL`: the one place that knows where a person's terminal actually is."""
    body = _fn("workspace_invite")
    assert "/join?i=" not in body and "CANONICAL" not in body
    _http, out = _invite(monkeypatch, routes={"/api/workspace/invite": (200, MAILED)},
                         slug=WS, email=THEM, role="reader")
    assert out["link"] == "https://app.example.test/?invite=tok"


def test_the_answer_says_which_way_the_link_went(monkeypatch):
    """`mailed` and `link` are two different next moves for the person holding the chat, and an
    answer that blurred them would have the agent tell somebody a mail was sent that was not."""
    _http, mailed = _invite(monkeypatch, routes={"/api/workspace/invite": (200, MAILED)},
                            slug=WS, email=THEM, role="reader")
    _http2, handed = _invite(monkeypatch, routes={"/api/workspace/invite": (200, HANDED)},
                             slug=WS, email=THEM, role="reader")
    assert mailed["delivery"] == "mailed" and handed["delivery"] == "link"
    assert handed["internal"] is True


# ── refusals ─────────────────────────────────────────────────────────────────────────────────────
def test_a_refused_invite_is_said_plainly_and_not_routed_around(monkeypatch):
    http, out = _invite(monkeypatch,
                        routes={"/api/workspace/invite": (403, {"detail": "insufficient role"})},
                        slug=WS, email=THEM, role="owner")
    assert out["refused"] == "not_invited" and out["status"] == 403
    assert "insufficient role" in json.dumps(out["why"])
    # THE ONE THING THAT MUST NOT HAPPEN: the older mint route runs no owner gate, so a fallback
    # onto it would turn this refusal into a grant.
    assert http.urls("/api/workspace/invites") == []


def test_a_refused_change_says_nothing_moved(monkeypatch):
    _http, out = _membership(
        monkeypatch,
        routes={"/api/workspace/membership": (409, {"detail": "cannot remove the last owner"})},
        slug=WS, email=THEM, role="remove")
    assert out["refused"] == "not_changed" and out["status"] == 409
    assert "last owner" in json.dumps(out["why"])
    assert "nothing changed" in out["tell_your_person"]


def test_the_old_remove_verb_is_gone_and_its_route_is_unreached():
    """`workspace_remove(slug, member)` took a SUBJECT ID and hit `DELETE /api/workspace/members/…`.
    Keeping it beside `workspace_membership` would leave two verbs for one act, addressed two ways,
    with one gate between them — which is the shape #1621's own lesson is about."""
    assert "workspace_remove" not in _tool_names()
    assert "/api/workspace/members/" not in _fn("workspace_membership")


# ── the words ────────────────────────────────────────────────────────────────────────────────────
def test_the_roster_reads_back_the_word_the_screen_uses(monkeypatch):
    """The store spells the read-only rank `viewer`; every surface a person sees says `reader`."""
    http = as_user(monkeypatch, uid="7", routes={
        "/api/workspace/members": (200, {"members": [
            {"subject": "u_them", "email": THEM, "role": "viewer"},
            {"subject": "u_o", "email": "owner@example.test", "role": "owner"}]})})
    out = json.loads(tool("workspace_members")(slug=WS))
    assert [m["role"] for m in out["members"]] == ["reader", "owner"]
    assert http.urls("/api/workspace/members")


def test_both_verbs_tell_the_agent_to_ask_and_confirm_before_calling():
    """The founder's ruling is that membership is a CONVERSATION. The docstring is the whole of what
    the model reads before it decides how to use the tool, so ask-then-confirm has to be in it."""
    add = _doc("workspace_invite")
    assert "confirm" in add.lower() and "NEVER GUESS AN ADDRESS" in add
    for word in ("owner", "contributor", "reader"):
        assert word in add
    change = _doc("workspace_membership")
    assert "confirm" in change.lower() and "remove" in change
    assert "last owner" in change.lower()


def test_neither_verb_defaults_its_workspace():
    """`_TARGET_DEFAULTING` is for WRITES, where an omitted slug means "wherever this conversation is
    working". On a membership change it would mean putting a stranger into whichever group happened
    to be open — and the rig's own comment beside that set already said so before these verbs
    existed."""
    block = _src().split("_TARGET_DEFAULTING = ", 1)[1].split("\n\n", 1)[0]
    assert "workspace_invite" not in block and "workspace_membership" not in block
