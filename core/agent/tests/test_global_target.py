"""THE ADMIN CAN AIM A CHAT AT THE COMPANY LAYER (Vexa-ai/vexa#1616) — the server half.

Founder, 2026-09-06 15:20Z, as the admin, on the header's `+` menu:

    *"as admin i should just have global as option to choose here as workspace to write to"*

Two things have to be true here, and they are the two sections below:

  1. a turn aimed at `_global` is TOLD what the company layer is for — by name, never by slug, with
     the one sentence that stops the tier filling up with meeting notes;
  2. only an admin can aim a chat there. `_read_target` cannot make that call: it answers `_global`
     to every subject on purpose, because the tier is mounted read-only into every worker and the
     read API mirrors that — `write=True` never narrowed it. So the target route asks the same
     question the file and entity routes ask, and asks it here rather than at the first write.

The client half — the `+` menu entry, the chip and its click — is pinned in
`clients/terminal/src/minutes/__tests__/globalTarget.test.tsx`.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.api_shared import GLOBAL_TARGET_NOTE, target_preamble
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

ADMIN = "u_admin"
MEMBER = "u_priya"


# ── 1. the sentence a turn writing there carries ─────────────────────────────────────────────────

def test_the_note_says_what_the_company_layer_is_for():
    """The seed's own words. A turn told only "writes go here" would fill the organisation tier
    with meeting notes — the one thing its README says it is not for."""
    out = target_preamble("Company", ["your own desk"], note=GLOBAL_TARGET_NOTE)
    assert "target workspace: Company — writes go here unless asked otherwise" in out
    assert "The company layer is thin" in out
    assert "`kg/entities/`" in out
    assert "never here" in out


def test_a_target_with_no_note_reads_exactly_as_it_always_did():
    """The note is additive. Every other target's line is the same bytes it was before #1616."""
    assert target_preamble("OeNB", ["ILM"]) == (
        "## Where this turn writes\n\n"
        "target workspace: OeNB — writes go here unless asked otherwise; "
        "ILM are mounted to read; write there only on an explicit ask with its purpose.\n\n")
    assert target_preamble("OeNB", ["ILM"], note="   ") == target_preamble("OeNB", ["ILM"])


def test_the_note_needs_a_target_to_hang_on():
    """No target, no line — a rule about a place nobody is writing to is furniture."""
    assert target_preamble("", ["ILM"], note=GLOBAL_TARGET_NOTE) == ""


# ── the app, over fakes ──────────────────────────────────────────────────────────────────────────

class _FakeRuntime:
    def __init__(self):
        self.envs: list[dict] = []

    def spawn(self, workload_id, profile, env):
        self.envs.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def __init__(self, events):
        self.events = events

    def read(self, unit_id, resume=None):
        yield from self.events


QUIET_TURN = [{"type": "turn-complete"}]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    return {"root": root, "sessions": _Sessions(), "runtime": _FakeRuntime()}


def _client(stack, *, admins: str = ""):
    """`global_admin_subjects` is the operator override `global_layer.is_admin` consults first — the
    one door into that answer that does not need an admin-api on the other end of a socket."""
    settings = load_settings(workspaces_dir=str(stack["root"]),
                             global_system_workspace_path=str(stack["root"] / "_global"),
                             global_admin_subjects=admins,
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, stack["runtime"], _FakeIdentity()),
                     stream_reader=_Reader(QUIET_TURN),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"])
    return TestClient(app)


def _turn(client, subject, session, prompt="hello"):
    return client.post("/api/chat", json={"prompt": prompt, "session": session},
                       headers={"X-User-Id": subject})


def _aim(client, subject, session, workspace="_global"):
    return client.post("/api/chat/target", json={"session": session, "workspace": workspace},
                       headers={"X-User-Id": subject})


def _sent_prompt(stack, n=-1):
    return json.loads(stack["runtime"].envs[n]["VEXA_START"])["entrypoint"]["inline"]


def _row(client, subject, session):
    rows = client.get("/api/sessions", headers={"X-User-Id": subject}).json()["sessions"]
    return next(r for r in rows if r["session"] == session)


# ── 2. who may aim a chat there ──────────────────────────────────────────────────────────────────

def test_the_admin_aims_a_chat_at_the_company_layer(stack):
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    r = _aim(client, ADMIN, "pchat-admin")
    assert r.status_code == 200 and r.json()["target"] == "_global"
    assert _row(client, ADMIN, "pchat-admin")["target"] == "_global"


def test_a_non_admin_session_cannot_set_global_as_target(stack):
    """The refusal this issue turns on. Before it, `_read_target` answered `_global` to everybody —
    it is mounted read-only into every worker and the read API mirrors that — so a chat could be
    pointed at a tier its person may not write, and the promise the field makes ("the agent may
    trust it") would break at the first write instead of here."""
    client = _client(stack, admins=ADMIN)
    _turn(client, MEMBER, "pchat-member")
    r = _aim(client, MEMBER, "pchat-member")
    assert r.status_code == 403
    assert "admin" in r.json()["detail"]
    assert _row(client, MEMBER, "pchat-member")["target"] is None


def test_the_refusal_leaves_a_target_the_person_legitimately_had(stack):
    """A refused chip moves nothing — not to the desk, not anywhere."""
    client = _client(stack, admins=ADMIN)
    _turn(client, MEMBER, "pchat-member")
    assert _aim(client, MEMBER, "pchat-member", workspace="").status_code == 200
    assert _aim(client, MEMBER, "pchat-member").status_code == 403
    assert _row(client, MEMBER, "pchat-member")["target"] is None


def test_an_instance_with_no_admin_configured_refuses_everybody(stack):
    """Fail closed. `is_admin` returns False when it cannot resolve the role, so nobody is handed
    the company layer by an unreachable oracle."""
    client = _client(stack)
    _turn(client, ADMIN, "pchat-admin")
    assert _aim(client, ADMIN, "pchat-admin").status_code == 403


def test_the_desk_is_still_one_click_back_for_the_admin(stack):
    """`""` is the desk, and it is how an admin stops writing into the company layer — the chip
    carries no ×, so this route is the way out."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    r = _aim(client, ADMIN, "pchat-admin", workspace="")
    assert r.status_code == 200 and r.json()["target"] is None


# ── 3. what the next turn is told ────────────────────────────────────────────────────────────────

def test_the_next_turn_is_told_the_company_layer_and_the_rule(stack):
    """The NAME is the registry's, not a literal in the router: `workspace_ids._default_name` gives
    the tier one until the setup conversation writes the company's own, at which point the line
    reads that instead. The router's own fallback is a safety net for a registry that cannot
    answer, and its only job is to keep the slug out of the sentence."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    _turn(client, ADMIN, "pchat-admin", prompt="write the company's principles")
    prompt = _sent_prompt(stack)
    assert "target workspace: The organisation — writes go here unless asked otherwise" in prompt
    assert "The company layer is thin" in prompt
    assert "`kg/entities/`" in prompt


def test_the_company_layer_is_NAMED_never_slugged(stack):
    """#1585/#1602's rule, on the one line every turn carries. `_global` has no registry row — it is
    a tier, not a workspace anybody made — so without a fallback the slug would print here."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    _turn(client, ADMIN, "pchat-admin", prompt="write the company's principles")
    where = _sent_prompt(stack).split("## Where this turn writes")[1].split("\n\n")[1]
    assert "_global" not in where


def test_the_desk_stays_readable_from_a_chat_writing_into_the_company_layer(stack):
    """*"note this on my desk"* keeps working — the same half of the rule #1611 wrote, and the
    reason the note says where the OTHER things belong rather than only what may not be written."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    _turn(client, ADMIN, "pchat-admin", prompt="and note this on my desk")
    assert "are mounted to read" in _sent_prompt(stack)


def test_the_worker_is_handed_the_company_layer_as_its_tools_default(stack):
    """WHERE THE WRITES ACTUALLY LAND. The target rides the delegation token as the slug a workspace
    verb with no `slug` of its own defaults to (Vexa-ai/vexa#1611) — so `workspace_write` and
    `entity_upsert` from this turn land in `_global`, where the file and entity routes run the same
    admin test again. A default, never a grant: the routes decide, not the token."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    _turn(client, ADMIN, "pchat-admin", prompt="write the company's principles")
    assert stack["runtime"].envs[-1]["VEXA_TARGET_WORKSPACE"] == "_global"


def test_the_turns_cwd_still_stays_off_the_system_tiers(stack):
    """THE BOUNDARY, PINNED RATHER THAN MOVED. `_worker_cwd` refuses a `global`/`system` mount as a
    cwd (Vexa-ai/vexa#1611) — so a target of `_global` moves the tools' default and the prompt's
    sentence, and a bare relative `Write` still lands on the desk. That is one commit's deliberate
    rule and not this issue's to rewrite; #1616 asked for the menu, the chip and the target."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-admin")
    _aim(client, ADMIN, "pchat-admin")
    _turn(client, ADMIN, "pchat-admin", prompt="write the company's principles")
    assert not stack["runtime"].envs[-1]["VEXA_WORKSPACE_PATH"].endswith("_global")


def test_a_chat_that_is_not_aimed_there_carries_no_rule_about_it(stack):
    """The note rides on the target, not on the mount — `_global` is mounted in every chat, and a
    sentence about a place this turn is not writing to is the chrome #1611 spent itself removing."""
    client = _client(stack, admins=ADMIN)
    _turn(client, ADMIN, "pchat-plain", prompt="hello")
    assert "The company layer is thin" not in _sent_prompt(stack)
