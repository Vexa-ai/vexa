"""The MCP delegation credential — mint, verify, expire, revoke, scope; and its two wiring seams.

WHAT THIS PROTECTS. A chat worker reaches the vexa-control MCP with a token the DISPATCHER minted for
exactly that dispatch, instead of a durable user credential baked into a config. Three properties have
to hold or the scheme is worse than what it replaced: a forged/expired/revoked token must be REFUSED
(not silently accepted), an unwatched dispatch must not receive an unbounded grant, and the whole
thing must stay OFF unless an operator configured both halves — because a half-configured credential
path that silently degrades to "no auth" is exactly how a security control stops being one.
"""
from __future__ import annotations

import json
import time

import pytest

from shared import delegation as d
from shared.config import load_settings
from control_plane.dispatch import build_unit_env

SECRET = "test-delegation-secret"

INV = {
    "identity": {"subject": "58", "launcher": "user:58"},
    "runner": "claude-code",
    "workspaces": [{"id": "58", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


def _inv(**over):
    return {**INV, **over}


def _settings(**over):
    return load_settings(**{"mcp_url": "https://rig.example/mcp",
                            "mcp_delegation_secret": SECRET, **over})


# ── the token itself ─────────────────────────────────────────────────────────

def test_mint_verify_roundtrip_carries_the_claims_a_verifier_needs():
    tok = d.mint_delegation(SECRET, subject="58", regime="human")
    claims = d.verify_delegation(SECRET, tok)
    assert claims["sub"] == "58"
    assert claims["aud"] == d.AUDIENCE          # audience pin: this token is for the MCP, nothing else
    assert claims["scope"] == {"regime": "human", "workspaces": "*"}
    assert claims["exp"] > claims["iat"] and claims["jti"]


def test_token_is_recognizable_without_parsing_it():
    """The rig has to route a bearer value to the right scheme BEFORE it can parse it — a failed parse
    must never be confused with a failed auth."""
    assert d.is_delegation_token(d.mint_delegation(SECRET, subject="58"))
    assert not d.is_delegation_token("vxa_mcp_a_durable_rig_token")
    assert not d.is_delegation_token("")


def test_wrong_secret_is_refused():
    tok = d.mint_delegation(SECRET, subject="58")
    with pytest.raises(d.BadSignature):
        d.verify_delegation("some-other-secret", tok)


def test_tampered_payload_is_refused():
    """The whole point of signing: editing the subject must not survive verification."""
    tok = d.mint_delegation(SECRET, subject="58")
    head, payload, sig = tok[len(d.PREFIX):].split(".")
    claims = json.loads(d._unb64u(payload))
    claims["sub"] = "1"                                    # promote yourself to another account
    forged = d.PREFIX + head + "." + d._b64u(d._canon(claims)) + "." + sig
    with pytest.raises(d.BadSignature):
        d.verify_delegation(SECRET, forged)


def test_expired_token_is_refused():
    past = int(time.time()) - 10_000
    tok = d.mint_delegation(SECRET, subject="58", ttl_sec=60, now=past)
    with pytest.raises(d.Expired):
        d.verify_delegation(SECRET, tok)


def test_token_valid_until_its_exp_and_not_one_second_past_it():
    now = int(time.time())
    tok = d.mint_delegation(SECRET, subject="58", ttl_sec=100, now=now)
    assert d.verify_delegation(SECRET, tok, now=now + 99)["sub"] == "58"
    with pytest.raises(d.Expired):
        d.verify_delegation(SECRET, tok, now=now + 100)


def test_revoked_jti_is_refused_even_though_the_signature_is_good():
    """Revocation is the answer to 'this token must die BEFORE its exp' — the signature still verifies,
    so only the denylist can stop it."""
    tok = d.mint_delegation(SECRET, subject="58", jti="revoke-me")
    assert d.verify_delegation(SECRET, tok)["jti"] == "revoke-me"
    with pytest.raises(d.Revoked):
        d.verify_delegation(SECRET, tok, revoked=["revoke-me"])
    # an unrelated entry on the denylist must not refuse a good token
    assert d.verify_delegation(SECRET, tok, revoked=["someone-else"])["sub"] == "58"


def test_a_token_for_another_audience_is_refused():
    now = int(time.time())
    claims = {"sub": "58", "aud": "some-other-service", "scope": {"regime": "human", "workspaces": "*"},
              "iat": now, "exp": now + 600, "jti": "x"}
    body = d._b64u(d._canon({"alg": "HS256", "typ": "vxdlg"})) + "." + d._b64u(d._canon(claims))
    import hashlib
    import hmac
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
    with pytest.raises(d.BadAudience):
        d.verify_delegation(SECRET, d.PREFIX + body + "." + d._b64u(sig))


def test_garbage_and_foreign_tokens_get_distinct_refusals():
    with pytest.raises(d.NotDelegated):
        d.verify_delegation(SECRET, "vxa_mcp_durable")      # the rig's own scheme — try that one next
    with pytest.raises(d.Malformed):
        d.verify_delegation(SECRET, "vxd_only.two")         # shaped like ours, and is not
    with pytest.raises(d.BadSignature):
        d.verify_delegation(SECRET, "vxd_aaa.bbb.ccc")


def test_an_empty_secret_is_fatal_on_both_sides():
    """A zero-length HMAC key 'works' — it would authenticate anyone who guessed the format. Unset must
    mean the feature is OFF, never 'signed with nothing'."""
    with pytest.raises(ValueError):
        d.mint_delegation("", subject="58")
    with pytest.raises(ValueError):
        d.verify_delegation("", d.mint_delegation(SECRET, subject="58"))


def test_subject_is_required():
    with pytest.raises(ValueError):
        d.mint_delegation(SECRET, subject="")


# ── regime + scope ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("trigger,regime", [
    ("message", "human"),                 # a person is in the loop this turn
    ("scheduled", "autonomous"),          # a routine fired with nobody watching
    ("event", "autonomous"),
    ("transcription", "autonomous"),
])
def test_regime_is_derived_from_the_unit_trigger(trigger, regime):
    assert d.regime_for_trigger(trigger) == regime


def test_an_autonomous_dispatch_may_not_carry_an_unbounded_grant():
    """The soft "*" scope is only defensible because a human can see and correct the turn. Unwatched,
    it must be the exact isolation set — and a dispatcher that asks for "*" is a BUG worth raising,
    not something to silently narrow."""
    with pytest.raises(ValueError):
        d.mint_delegation(SECRET, subject="58", regime="autonomous", workspaces="*")


def test_autonomous_scope_admits_only_its_own_workspaces():
    tok = d.mint_delegation(SECRET, subject="58", regime="autonomous", workspaces=["alpha", "beta"])
    claims = d.verify_delegation(SECRET, tok)
    assert claims["scope"] == {"regime": "autonomous", "workspaces": ["alpha", "beta"]}
    assert d.scope_allows_workspace(claims, "alpha")
    assert not d.scope_allows_workspace(claims, "gamma")


def test_human_scope_admits_everything_and_a_broken_scope_admits_nothing():
    human = d.verify_delegation(SECRET, d.mint_delegation(SECRET, subject="58", regime="human"))
    assert d.scope_allows_workspace(human, "anything-at-all")
    for broken in ({}, {"scope": None}, {"scope": {}}, {"scope": {"workspaces": 7}}):
        assert not d.scope_allows_workspace(broken, "alpha")   # fails CLOSED


def test_an_unknown_regime_is_refused():
    with pytest.raises(ValueError):
        d.mint_delegation(SECRET, subject="58", regime="semi-autonomous")


# ── seam 1: the dispatcher mints it into the worker env ──────────────────────

def _env(settings, inv):
    return build_unit_env(settings, inv, unit_id="u1", token="dispatch-token")


def test_a_human_chat_dispatch_gets_a_soft_scoped_token_for_its_subject():
    env = _env(_settings(), _inv(trigger="message"))
    assert env["VEXA_MCP_URL"] == "https://rig.example/mcp"
    claims = d.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["sub"] == "58"
    assert claims["scope"] == {"regime": "human", "workspaces": "*"}


def test_an_autonomous_dispatch_gets_the_hard_isolation_set():
    inv = _inv(trigger="scheduled", workspaces=[{"id": "58", "mode": "rw"}, {"id": "ops", "mode": "ro"}])
    claims = d.verify_delegation(SECRET, _env(_settings(), inv)["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["scope"] == {"regime": "autonomous", "workspaces": ["58", "ops"]}
    assert not d.scope_allows_workspace(claims, "somebody-elses-workspace")


def test_the_minted_token_honours_the_configured_ttl():
    env = _env(_settings(mcp_delegation_ttl_sec=120), _inv())
    claims = d.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["exp"] - claims["iat"] == 120


@pytest.mark.parametrize("over", [
    {"mcp_delegation_secret": ""},   # no secret  → nothing to sign with
    {"mcp_url": ""},                 # no endpoint → nowhere to send it
])
def test_delegation_stays_off_unless_both_halves_are_configured(over):
    """Half-configured must mean OFF, not 'attached without a credential'."""
    env = _env(_settings(**over), _inv())
    assert "VEXA_MCP_DELEGATION_TOKEN" not in env
    assert "VEXA_MCP_URL" not in env


def test_two_dispatches_never_share_a_token_id():
    """jti is what revocation names, so it has to be per-dispatch or revoking one revokes the fleet."""
    a = d.verify_delegation(SECRET, _env(_settings(), _inv())["VEXA_MCP_DELEGATION_TOKEN"])
    b = d.verify_delegation(SECRET, _env(_settings(), _inv())["VEXA_MCP_DELEGATION_TOKEN"])
    assert a["jti"] != b["jti"]


# ── seam 2: the worker turns it into an MCP attachment ───────────────────────

def test_the_worker_writes_a_bearer_header_config_and_widens_its_allow_set(tmp_path, monkeypatch):
    from worker.engine import mcp_delegation_config, VEXA_MCP_SERVER
    tok = d.mint_delegation(SECRET, subject="58")
    monkeypatch.setenv("VEXA_MCP_URL", "https://rig.example/mcp")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", tok)
    path, tools = mcp_delegation_config(tmp_path)
    cfg = json.loads(open(path).read())["mcpServers"][VEXA_MCP_SERVER]
    assert cfg["url"] == "https://rig.example/mcp" and cfg["type"] == "http"
    # the credential travels in a HEADER — a query string leaks into every access log on the way
    assert cfg["headers"]["Authorization"] == f"Bearer {tok}"
    assert "?" not in cfg["url"]
    # attaching the server is not enough; the model must also be ALLOWED to call it
    #
    # ⚠ THIS ASSERTION WAS STALE AND FAILING ON EVERY RUN of the suite, on `minutes-mcp-viewer`,
    # since the 21-tool union landed. It said `== [prefix]`, which was true when the allow-set was
    # the prefix alone; the list grew and this line kept failing, red, in a suite whose other 721
    # tests are green. Found 2026-09-02 while adding `entity_upsert` to that same list. An anomaly
    # is a finding: the prefix + the named tools is the shape the code actually returns and the
    # shape it is meant to return, so the check now says that instead of a number that ages.
    from worker.engine import VEXA_MCP_TOOLS
    assert tools[0] == f"mcp__{VEXA_MCP_SERVER}"
    assert set(tools[1:]) == {f"mcp__{VEXA_MCP_SERVER}__{t}" for t in VEXA_MCP_TOOLS}
    # decision 24: the write-back phase is only as reliable as the tool being NAMED — a tool the
    # allow-set omits is one the model has to find before it can call it.
    assert f"mcp__{VEXA_MCP_SERVER}__entity_upsert" in tools


def test_the_config_lands_where_the_post_turn_commit_cannot_sweep_it_up(tmp_path, monkeypatch):
    """run_harness_turn does `git add -A` on every changed mount. `.claude/` is gitignored in the
    workspace seed, so a credential written there is never committed or synced to the store."""
    from pathlib import Path
    monkeypatch.setenv("VEXA_MCP_URL", "https://rig.example/mcp")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", d.mint_delegation(SECRET, subject="58"))
    from worker.engine import mcp_delegation_config
    path, _ = mcp_delegation_config(tmp_path)
    assert Path(path).parent.name == ".claude"
    # The seeds moved out of core/agent in 5aa8226aa (founder ruling: BEHAVIOR is a top-level peer
    # of the machinery) — machinery is what compiles into the runtime, behavior is what it loads.
    # The container path is unchanged; only this repo-relative lookup had to follow the move.
    seed = Path(__file__).resolve().parents[3] / "behavior" / "workspaces" / "default" / ".gitignore"
    assert ".claude/" in seed.read_text()


@pytest.mark.parametrize("env", [
    {"VEXA_MCP_URL": "https://rig.example/mcp"},                       # endpoint, no credential
    {"VEXA_MCP_DELEGATION_TOKEN": "vxd_x.y.z"},                        # credential, no endpoint
    {},                                                                # neither
])
def test_the_worker_attaches_nothing_when_the_dispatcher_minted_nothing(tmp_path, monkeypatch, env):
    from worker.engine import mcp_delegation_config
    monkeypatch.delenv("VEXA_MCP_URL", raising=False)
    monkeypatch.delenv("VEXA_MCP_DELEGATION_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert mcp_delegation_config(tmp_path) == (None, [])
    assert not (tmp_path / ".claude" / "mcp.json").exists()


def test_the_turn_runner_forwards_the_attachment_to_the_harness(monkeypatch, tmp_path):
    """The seam that did not exist: build_argv accepted mcp_config, run_turn_over_workspace did not
    forward one, so no chat worker ever attached an MCP.

    `mcp_preflight` is stubbed to True here — this test is about the FORWARDING seam, not the F153
    reachability guard (that guard, and the case where it says no, are `test_mcp_reconnect.py`'s)."""
    import worker.engine as engine
    seen = {}

    def fake_run_harness_turn(work, prompt, harness, **kw):
        seen.update(kw)
        yield {"type": "done", "ok": True, "sessionId": "s1"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run_harness_turn)
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "harness_from_env", lambda: _StubHarness())
    monkeypatch.setattr(engine, "mcp_preflight", lambda url, headers, **kw: (True, ""))
    monkeypatch.setattr(engine, "_mcp_endpoint", lambda path: ("https://rig.example/mcp", {}))
    list(engine.run_turn_over_workspace(tmp_path, "hi", mcp_config="/ws/.claude/mcp.json"))
    assert seen["mcp_config"] == "/ws/.claude/mcp.json"


class _StubHarness:
    name = "stub"

    def prepare(self, work, chat_root=None):
        pass

    def transcript_bytes(self, work, session_id):
        return 0

    def preflight(self):
        return None
