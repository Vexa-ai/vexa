"""The invite LINK — where it points, and what an unbound invite means (Vexa-ai/vexa#1635).

The founder minted an invite in chat, opened the link, and read *"not found"*. Two defects, one
missing surface:

  * **the base was wrong** — the rig composed `https://rig.dev.vexa.ai/join?i=…` from the MCP host
    it publishes itself under. A client knows where IT is; only the deployment knows where the
    person's terminal is. So the link is composed HERE now, on the deployment's declared public app
    URL (``VEXA_UI_URL``), and handed back as ``invite_url``.
  * **an unbound invite was the default** — ``allowed_emails`` was stored and then ignored unless the
    caller also said ``mode="restricted"``, so naming one address produced a link anyone holding it
    could redeem, and nothing in the response said so.

ONE COMPOSER, TWO CALLERS. `workspace_membership.invite_link` is the only place that knows what an
invite link looks like: the older mint route (`POST /api/workspace/invites`) and the invite act
behind `workspace_invite` (Vexa-ai/vexa#1632, `membership_acts.invite`) both go through it. The act
briefly composed `<ui>/?invite=<token>` of its own — a second spelling of a path, which is the same
defect one layer in, and it pointed at a query nothing on `/` reads for a signed-out visitor.

(The page itself is the terminal's `/join`, tested in `clients/terminal/src/app/__tests__`.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, workspace_id: str) -> Path:
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _client(root: Path, index=None, *, ui_url: str = "https://app.example.com"):
    settings = load_settings()
    settings.ui_url = ui_url
    return TestClient(create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


@pytest.fixture()
def owned(tmp_path):
    _init_ws(tmp_path, "pilot-a1b2c3")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "pilot-a1b2c3", "owner1", index=idx, email="jane@example.com")
    return tmp_path, idx


# ── the base ────────────────────────────────────────────────────────────────────────────────────
def test_invite_url_is_built_on_the_declared_public_app_url(owned):
    """THE ONE ASSERTION THIS ISSUE IS ABOUT. The link a client hands to a person is composed by the
    service that knows the deployment, on ``VEXA_UI_URL`` — never on whatever host the client
    happens to be published under."""
    root, idx = owned
    c = _client(root, idx, ui_url="https://app.example.com")
    r = c.post("/api/workspace/invites", headers=_h("owner1"),
               json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"})
    assert r.status_code == 201
    body = r.json()
    assert body["join_path"] == "/join"
    assert body["invite_url"] == f"https://app.example.com/join?i={body['token']}"
    assert body["invite_url_refused"] is None


def test_a_trailing_slash_on_the_declared_url_does_not_double(owned):
    root, idx = owned
    c = _client(root, idx, ui_url="https://app.example.com/")
    body = c.post("/api/workspace/invites", headers=_h("owner1"),
                  json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"}).json()
    assert body["invite_url"].startswith("https://app.example.com/join?i=")


def test_no_declared_url_means_no_link_and_a_reason(owned):
    """A deployment that has not said where its terminal is gets NO url and is told which key names
    it — rather than a url with no origin, which is how the founder's link came to point nowhere."""
    root, idx = owned
    c = _client(root, idx, ui_url="")
    body = c.post("/api/workspace/invites", headers=_h("owner1"),
                  json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"}).json()
    assert body["invite_url"] is None
    assert "VEXA_UI_URL" in body["invite_url_refused"]
    assert body["token"]           # the token is still minted; only the composed link is missing


def test_the_public_app_url_is_a_declared_config_key():
    """`VEXA_UI_URL` is the deployment's declared public app URL — the config contract says so, and
    it is the SAME key the flows lane reads. A second spelling of the host is how a link ends up
    naming somewhere the person cannot reach."""
    import json
    contract = json.loads((Path(__file__).resolve().parents[1] /
                           "control_plane" / "config.v1.json").read_text())
    key = next(k for k in contract["keys"] if k["key"] == "VEXA_UI_URL")
    assert "terminal" in key["description"]


# ── bound by default ────────────────────────────────────────────────────────────────────────────
def test_named_addresses_bind_the_invite_without_being_asked_twice(owned):
    """`allowed_emails` USED to be stored and ignored unless `mode="restricted"` came with it, so a
    mint that named one address produced a link anyone could redeem. Naming addresses IS the
    binding now."""
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor",
                          "allowed_emails": ["jsmith@example.com"]}).json()
    assert minted["mode"] == "restricted"
    # …and it is ENFORCED at the redeem, which is the half that matters.
    bad = c.post("/api/workspace/invites/accept",
                 headers={"X-User-Id": "u_bob", "X-User-Email": "bob@example.com"},
                 json={"token": minted["token"]})
    assert bad.status_code == 403
    ok = c.post("/api/workspace/invites/accept",
                headers={"X-User-Id": "u_jsmith", "X-User-Email": "jsmith@example.com"},
                json={"token": minted["token"]})
    assert ok.status_code == 200 and ok.json()["role"] == "contributor"


def test_addresses_and_an_open_link_at_once_is_refused(owned):
    """"These addresses, and also anyone" is a contradiction. Resolving it silently in either
    direction gives the caller something other than what they asked for, and only one of the two
    directions is safe — so neither is chosen."""
    root, idx = owned
    c = _client(root, idx)
    r = c.post("/api/workspace/invites", headers=_h("owner1"),
               json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open",
                     "allowed_emails": ["jsmith@example.com"]})
    assert r.status_code == 400
    assert "bound" in r.json()["detail"]


def test_an_explicitly_open_invite_is_still_open(owned):
    """The escape hatch stays open — an admin who asks for a link they can pass to anyone gets one.
    It is the DEFAULT that changed, not the capability."""
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"}).json()
    assert minted["mode"] == "open"
    ok = c.post("/api/workspace/invites/accept", headers=_h("u_anyone"),
                json={"token": minted["token"]})
    assert ok.status_code == 200


# ── the preview the join page renders from ──────────────────────────────────────────────────────
def test_preview_carries_the_sentence_the_join_page_says(owned):
    """*Jane invited you to pilot as a contributor* — the name, the inviter and the role. The name is
    the workspace's, not its directory: `pilot-a1b2c3` is not a name anybody was told. Unregistered,
    it falls back to the slug rather than to nothing."""
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor",
                          "allowed_emails": ["jsmith@example.com"]}).json()
    p = c.get("/api/workspace/invites/preview", params={"token": minted["token"]}).json()
    assert p["workspace_id"] == "pilot-a1b2c3"
    assert p["name"] == "pilot-a1b2c3"          # no registry record here → the slug, never blank
    assert p["role"] == "contributor"
    assert p["shared_by"] == "jane@example.com"
    assert p["valid"] is True and p["reason"] is None
    # the address the page prefills and LOCKS: without it the person types one, gets a 403 at the
    # redeem, and nothing on screen says which address the invite was for.
    assert p["restricted_to"] == ["jsmith@example.com"]


def test_preview_of_an_open_invite_discloses_no_address(owned):
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"}).json()
    p = c.get("/api/workspace/invites/preview", params={"token": minted["token"]}).json()
    assert p["mode"] == "open" and p["restricted_to"] == []


def test_preview_needs_no_session(owned):
    """The card renders for somebody with no account on this instance: it is gated by the token, not
    by a subject. Asking a stranger to sign in to find out what they are signing in FOR is both the
    wrong order and how an invite reads as a phish."""
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open"}).json()
    r = c.get("/api/workspace/invites/preview", params={"token": minted["token"]})  # no X-User-Id
    assert r.status_code == 200


def test_preview_of_a_spent_invite_says_which_and_never_404s(owned):
    """A spent token is a thing that HAPPENED. The page turns `used_up` into one sentence; a 404
    here would make it say "not valid" about an invite that was perfectly good yesterday."""
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor", "mode": "open", "max_uses": 1}).json()
    assert c.post("/api/workspace/invites/accept", headers=_h("u_first"),
                  json={"token": minted["token"]}).status_code == 200
    p = c.get("/api/workspace/invites/preview", params={"token": minted["token"]})
    assert p.status_code == 200
    assert p.json()["valid"] is False and p.json()["reason"] == "used_up"


def test_preview_of_an_expired_invite_says_expired(owned):
    root, idx = owned
    c = _client(root, idx)
    minted = c.post("/api/workspace/invites", headers=_h("owner1"),
                    json={"workspace_id": "pilot-a1b2c3", "role": "contributor",
                          "mode": "open", "expires_in_sec": 1}).json()
    info = m.preview_invite(root, minted["token"], now=9_999_999_999)
    assert info["valid"] is False and info["reason"] == "expired"


def test_preview_of_a_token_that_matches_nothing_is_404(owned):
    """The one case that IS a 404 upstream, deliberately: answering anything else would say whether
    a workspace exists. The page renders it as "this invite link is not valid"."""
    root, idx = owned
    c = _client(root, idx)
    assert c.get("/api/workspace/invites/preview", params={"token": "nope"}).status_code == 404


# ── one composer ────────────────────────────────────────────────────────────────────────────────
def test_invite_link_is_the_only_thing_that_knows_the_path():
    """Two callers compose an invite link; both call this. The path is `/join`, the parameter is
    `i`, and the token is percent-encoded because a base64url token is not URL-safe by assumption."""
    assert m.JOIN_PATH == "/join"
    assert m.invite_link("https://app.example.com", "tok") == "https://app.example.com/join?i=tok"
    assert m.invite_link("https://app.example.com/", "tok") == "https://app.example.com/join?i=tok"
    assert m.invite_link("  https://app.example.com  ", "tok") == "https://app.example.com/join?i=tok"
    assert m.invite_link("https://app.example.com", "a/b+c") == "https://app.example.com/join?i=a%2Fb%2Bc"


def test_no_origin_is_no_link_rather_than_a_path():
    """An empty base returns "", never "/join?i=…" — a path with no host is the shape a client
    silently turns into ITS OWN origin, which is how a link ends up naming the wrong server."""
    assert m.invite_link("", "tok") == ""
    assert m.invite_link(None, "tok") == ""


def test_the_invite_act_composes_through_the_same_one(tmp_path):
    """`workspace_invite`'s route (Vexa-ai/vexa#1632) hands the agent a link to give a person. It is
    the same link the join page serves — asserted here rather than assumed, because these two landed
    in the same hour from two different sessions."""
    from control_plane import membership_acts

    _init_ws(tmp_path, "pilot-a1b2c3")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "pilot-a1b2c3", "owner1", index=idx, email="jane@example.com")
    out = membership_acts.invite(
        tmp_path, "pilot-a1b2c3", email="jsmith@example.com", role="contributor",
        inviter="owner1", index=idx, ui_url="https://app.example.com")

    assert out["link"].startswith("https://app.example.com/join?i=")
    assert "?invite=" not in out["link"]
    # and it is redeemable at the page's own preview, which is what "the link works" means
    token = out["link"].split("i=", 1)[1]
    info = m.preview_invite(tmp_path, token)
    assert info is not None and info["valid"] is True
    assert info["allowed_emails"] == ["jsmith@example.com"]
