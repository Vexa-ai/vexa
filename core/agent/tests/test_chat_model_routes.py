"""A CHAT RUNS ON THE MODEL ITS PERSON PICKED — the picker's routes, end to end over a real app.

Four things have to be true:

  1. `GET /api/models/catalog` lists what THIS person may pick (an admins-only model is absent for a
     member), their default, and — asked for a chat — that chat's pick; never an endpoint or a key;
  2. `POST /api/chat/model` stores a pick only when the next turn could run it, refuses one it could
     not with a typed fault, and forces the chat's next turn onto a fresh worker;
  3. the chat's next turn is dispatched on that pick, through the provider port, and a pick that can
     no longer run refuses the turn out loud — `source: model-provider`, the model and the provider
     named — before anything is spawned;
  4. the Settings → Models Test button probes the chosen entry through the same port.

L2: a real FastAPI app over fakes; no redis, no runtime, no model.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane import config_test
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.model_providers import parse
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE

MEMBER, ADMIN = "u_member", "u_admin"


class _Runtime:
    def __init__(self):
        self.envs: list[dict] = []
        self.spawned: list[str] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(workload_id)
        self.envs.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _ModelConfig:
    """admin-api's effective Settings → Models, per subject."""

    def __init__(self, by_subject=None):
        self.by_subject = by_subject or {}

    def resolve(self, subject):
        return dict(self.by_subject.get(subject, {}))


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


class _Dispatcher(Dispatcher):
    """The real dispatcher; only the admin role is answered locally rather than by admin-api."""

    def is_admin(self, subject):
        return subject == ADMIN


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-key")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    runtime = _Runtime()
    model_config = _ModelConfig()
    dispatcher = _Dispatcher(load_settings(workspaces_dir=str(root)), runtime, _Identity(),
                             model_config=model_config, catalog=parse(json.dumps(EXAMPLE), ENV))
    sessions = _Sessions()
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=sessions)
    return {"client": TestClient(app), "runtime": runtime, "sessions": sessions,
            "model_config": model_config, "dispatcher": dispatcher}


def _as(who):
    return {"X-User-Id": who}


# ── 1. the catalog a person sees ────────────────────────────────────────────────────────────────

def test_a_member_sees_only_what_they_may_pick(stack):
    body = stack["client"].get("/api/models/catalog", headers=_as(MEMBER)).json()
    assert [m["id"] for m in body["models"]] == ["qwen3-32b", "claude"]
    assert body["default"] == "qwen3-32b" and "selected" not in body


def test_an_admin_sees_the_admins_only_model(stack):
    body = stack["client"].get("/api/models/catalog", headers=_as(ADMIN)).json()
    assert "or-sonnet" in [m["id"] for m in body["models"]]


def test_an_own_endpoint_adds_no_entry_to_the_list(stack):
    """The list is the catalog's real models only — never a placeholder for an own endpoint."""
    stack["model_config"].by_subject[MEMBER] = {"mode": "custom",
                                                "base_url": "https://openrouter.ai/api/v1"}
    body = stack["client"].get("/api/models/catalog", headers=_as(MEMBER)).json()
    assert [m["id"] for m in body["models"]] == ["qwen3-32b", "claude"]


def test_the_listing_never_carries_an_endpoint_or_a_key(stack):
    text = stack["client"].get("/api/models/catalog", headers=_as(ADMIN)).text
    for leaked in ("10.0.0.5", "openrouter.ai", ENV["VEXA_MODEL_SECRET_OPENROUTER"], "env:", "extra_body"):
        assert leaked not in text


def test_a_persons_own_default_is_their_default(stack):
    stack["model_config"].by_subject[MEMBER] = {"default_model": "claude"}
    assert stack["client"].get("/api/models/catalog", headers=_as(MEMBER)).json()["default"] == "claude"


# ── 2. picking ──────────────────────────────────────────────────────────────────────────────────

def test_picking_a_model_stores_it_and_the_chat_lists_it(stack):
    c = stack["client"]
    r = c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as(MEMBER))
    assert r.status_code == 200 and r.json() == {"ok": True, "session": "s1", "model": "claude",
                                                 "effort": None, "changed": True}
    assert c.get("/api/models/catalog?session=s1", headers=_as(MEMBER)).json()["selected"] == "claude"
    # a chat is listed once somebody wrote in it (#1783): a pick alone lists nothing
    stack["sessions"].upsert(MEMBER, "s1", title="Pricing prep")
    row = next(x for x in c.get("/api/sessions", headers=_as(MEMBER)).json()["sessions"]
               if x["session"] == "s1")
    assert row["model"] == "claude"


def test_picking_forces_the_next_turn_onto_a_fresh_worker(stack):
    """A running worker keeps the model it started with; the pick steps the chat's unit."""
    c, sess = stack["client"], stack["sessions"]
    c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as(MEMBER))
    assert sess.take_mount_generation(MEMBER, "s1") == 1
    again = c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as(MEMBER))
    assert again.json()["changed"] is False
    assert sess.take_mount_generation(MEMBER, "s1") == 1        # a re-pick costs no cold start


@pytest.mark.parametrize("model,status,kind", [("retired-model", 422, "unknown_model"),
                                               ("or-sonnet", 403, "not_permitted"),
                                               # the retired own-endpoint id is no model at all
                                               ("mine", 422, "unknown_model")])
def test_a_pick_the_next_turn_could_not_run_is_refused_and_not_stored(stack, model, status, kind):
    c = stack["client"]
    r = c.post("/api/chat/model", json={"session": "s1", "model": model}, headers=_as(MEMBER))
    assert r.status_code == status
    fault = r.json()["fault"]
    assert (fault["source"], fault["kind"], fault["model"]) == ("model-provider", kind, model)
    assert stack["sessions"].model(MEMBER, "s1") == ""


def test_an_empty_pick_puts_the_chat_back_on_the_default(stack):
    c = stack["client"]
    c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as(MEMBER))
    r = c.post("/api/chat/model", json={"session": "s1", "model": ""}, headers=_as(MEMBER))
    assert r.json()["model"] is None and stack["sessions"].model(MEMBER, "s1") == ""


# ── 3. the turn ─────────────────────────────────────────────────────────────────────────────────

def _turn(c, who, session="s1"):
    return c.post("/api/chat", json={"prompt": "hello", "session": session}, headers=_as(who))


def test_the_next_turn_runs_on_the_pick(stack):
    c = stack["client"]
    c.post("/api/chat/model", json={"session": "s1", "model": "qwen3-32b"}, headers=_as(MEMBER))
    assert _turn(c, MEMBER).status_code == 200
    env = stack["runtime"].envs[-1]
    assert env["VEXA_RUNNER"] == "openai-agent"
    # the endpoint is the model proxy's to know (H5/W1): the worker names no provider and holds no key
    assert "10.0.0.5" not in env["VEXA_LLM_BASE_URL"] and env["VEXA_LLM_API_KEY"] == ""
    assert env["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B"
    assert stack["runtime"].spawned[-1].endswith("-g1")          # the fresh unit the pick asked for


def test_an_unpicked_chat_runs_on_the_persons_default(stack):
    stack["model_config"].by_subject[MEMBER] = {"default_model": "claude"}
    assert _turn(stack["client"], MEMBER).status_code == 200
    env = stack["runtime"].envs[-1]
    assert (env["VEXA_RUNNER"], env["VEXA_AGENT_MODEL"], env["ANTHROPIC_BASE_URL"]) == (
        "claude-code", "claude-sonnet-4-6", "")


def test_a_pick_that_can_no_longer_run_refuses_the_turn_with_a_typed_fault(stack):
    """The catalog changed under a stored pick: the turn is refused out loud, nothing is spawned,
    and the fault names the model — never a turn quietly run on some other model."""
    stack["sessions"].set_model(MEMBER, "s1", "retired-model")
    r = _turn(stack["client"], MEMBER)
    assert r.status_code == 422
    body = r.json()
    assert body["fault"]["source"] == "model-provider"
    assert body["fault"]["kind"] == "unknown_model" and body["fault"]["model"] == "retired-model"
    assert "retired-model" in body["detail"]
    assert stack["runtime"].spawned == []


def test_a_catalog_carries_its_own_credentials_past_the_deployment_preflight(stack, monkeypatch):
    """No deployment credential at all: the pre-catalog preflight refused every turn. A catalog's
    providers resolved their secrets at boot, so the turn runs."""
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
              "VEXA_LLM_API_KEY", "HOST_CLAUDE_CREDENTIALS", "HOST_CLAUDE_DIR"):
        monkeypatch.delenv(k, raising=False)
    stack["sessions"].set_model(MEMBER, "s1", "qwen3-32b")
    assert _turn(stack["client"], MEMBER).status_code == 200
    assert stack["runtime"].spawned


# ── 4. the Test button ──────────────────────────────────────────────────────────────────────────

def test_the_test_button_probes_the_chosen_entry_through_the_port(stack, monkeypatch):
    sent = []

    def fake_post(url, body, headers):
        sent.append((url, body, headers))
        return 200, "{}"

    monkeypatch.setattr(config_test, "_post", fake_post)
    out = stack["client"].get("/api/models/test?model=or-sonnet", headers=_as(ADMIN)).json()
    assert out["ok"] is True and out["model"] == "or-sonnet" and out["provider"] == "openrouter"
    url, body, headers = sent[-1]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers == {"Authorization": f"Bearer {ENV['VEXA_MODEL_SECRET_OPENROUTER']}"}
    assert body["model"] == "anthropic/claude-sonnet-4.5"


def test_the_test_button_says_why_a_pick_cannot_run(stack):
    out = stack["client"].get("/api/models/test?model=or-sonnet", headers=_as(MEMBER)).json()
    assert out["ok"] is False and out["fault"]["kind"] == "not_permitted"


def _route(model_id, decl=EXAMPLE, cfg=None):
    from control_plane.dispatch import route_context
    return parse(json.dumps(decl), ENV).route(model_id, route_context(cfg or {}, env=ENV), admin=True)


def test_the_probe_speaks_each_routes_own_dialect_with_its_own_credential():
    sent = []

    def post(url, body, headers):
        sent.append((url, body, headers))
        return 200, "{}"

    config_test.run_route_test(_route("qwen3-32b"), post=post)
    url, body, headers = sent[-1]
    assert url == "http://10.0.0.5:8000/v1/chat/completions" and headers == {}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}   # the turn's extra body

    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["anthropic"] = {"adapter": "anthropic", "auth": "secret",
                                      "secret_ref": "env:VEXA_MODEL_SECRET_ANTHROPIC"}
    config_test.run_route_test(_route("claude", decl), post=post)
    url, _body, headers = sent[-1]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == ENV["VEXA_MODEL_SECRET_ANTHROPIC"] and "Authorization" not in headers


def test_the_subscription_entry_is_tested_by_its_credential_file(tmp_path):
    out = config_test.run_route_test(_route("claude"), creds_path=str(tmp_path / "absent.json"),
                                     post=lambda *a: pytest.fail("no request for a subscription"))
    assert out["ok"] is False and out["model"] == "claude" and "subscription" in out["summary"].lower()


def test_a_rejected_credential_names_the_model_and_provider():
    out = config_test.run_route_test(_route("or-sonnet"), post=lambda *a: (401, "no"))
    assert out["ok"] is False and out["status"] == 401
    assert out["summary"].startswith("or-sonnet via openrouter:")


ENDPOINT_BODY = '{"error":{"message":"upstream gw-7.internal.example rejected: quota for team lab exceeded"}}'


@pytest.mark.parametrize("status, kind", [(402, "unpaid"), (401, "unauthorized"), (500, "unavailable"),
                                          (400, "refused")])
def test_a_member_never_sees_the_operator_endpoints_body_or_address(status, kind):
    """The Test button on an operator's model, for someone who is not an instance admin: the
    verdict and the typed fault, never what the endpoint said nor where it is."""
    out = config_test.run_route_test(_route("qwen3-32b"), post=lambda *a: (status, ENDPOINT_BODY),
                                     admin=False)
    text = json.dumps(out)
    assert out["ok"] is False and out["fault"]["kind"] == kind and out["fault"]["status"] == status
    assert out["fault"]["source"] == "model-provider" and out["fault"]["model"] == "qwen3-32b"
    for leaked in ("gw-7", "quota for team", "10.0.0.5"):
        assert leaked not in text, leaked


def test_an_unreachable_operator_endpoint_is_typed_for_a_member_without_its_address():
    def boom(*a):
        raise OSError("connect to 10.0.0.5:8000 refused")
    out = config_test.run_route_test(_route("qwen3-32b"), post=boom, admin=False)
    assert out["fault"]["kind"] == "unavailable" and "10.0.0.5" not in json.dumps(out)


def test_an_admin_still_sees_the_endpoints_own_words():
    out = config_test.run_route_test(_route("qwen3-32b"), post=lambda *a: (400, ENDPOINT_BODY))
    assert "quota for team lab" in out["summary"] and "fault" not in out


def test_the_test_route_hides_the_body_from_a_member_and_shows_it_to_an_admin(stack, monkeypatch):
    monkeypatch.setattr(config_test, "_post", lambda *a: (400, ENDPOINT_BODY))
    c = stack["client"]
    member = c.get("/api/models/test?model=qwen3-32b", headers=_as(MEMBER)).json()
    admin = c.get("/api/models/test?model=qwen3-32b", headers=_as(ADMIN)).json()
    assert "quota for team" not in json.dumps(member) and member["fault"]["kind"] == "refused"
    assert "quota for team" in admin["summary"]


def test_a_chat_session_id_is_bounded_where_it_enters(stack):
    c = stack["client"]
    bad = "../" + "x" * 10
    assert c.post("/api/chat/model", json={"session": bad, "model": "claude"},
                  headers=_as(MEMBER)).status_code == 422
    assert c.get(f"/api/models/catalog?session={bad}", headers=_as(MEMBER)).status_code == 422


# ── the effort level, picked per chat (founder 2026-10-10) ─────────────────────────────────────

def test_the_listing_offers_effort_levels_only_on_a_model_that_has_them(stack):
    body = stack["client"].get("/api/models/catalog?session=s1", headers=_as(ADMIN)).json()
    caps = {m["id"]: m["capabilities"] for m in body["models"]}
    assert caps["claude"]["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert caps["or-sonnet"]["default_effort"] == "medium"
    assert body["selected_effort"] is None


def test_an_effort_pick_is_stored_with_the_model_and_runs_on_the_next_turn(stack):
    c = stack["client"]
    r = c.post("/api/chat/model", json={"session": "s1", "model": "claude", "effort": "high"},
               headers=_as(MEMBER))
    assert r.status_code == 200 and r.json()["effort"] == "high"
    listing = c.get("/api/models/catalog?session=s1", headers=_as(MEMBER)).json()
    assert (listing["selected"], listing["selected_effort"]) == ("claude", "high")
    assert _turn(c, MEMBER).status_code == 200
    assert stack["runtime"].envs[-1]["VEXA_AGENT_EFFORT"] == "high"


def test_changing_only_the_effort_forces_a_fresh_worker(stack):
    c, sess = stack["client"], stack["sessions"]
    c.post("/api/chat/model", json={"session": "s1", "model": "claude", "effort": "low"}, headers=_as(MEMBER))
    assert sess.take_mount_generation(MEMBER, "s1") == 1
    r = c.post("/api/chat/model", json={"session": "s1", "model": "claude", "effort": "max"}, headers=_as(MEMBER))
    assert r.json()["changed"] is True and sess.take_mount_generation(MEMBER, "s1") == 2


@pytest.mark.parametrize("model, effort", [("claude", "none"), ("qwen3-32b", "medium"),
                                           ("claude", "turbo")])
def test_an_effort_the_model_cannot_take_is_refused_at_pick_time_and_not_stored(stack, model, effort):
    c = stack["client"]
    r = c.post("/api/chat/model", json={"session": "s1", "model": model, "effort": effort},
               headers=_as(MEMBER))
    assert r.status_code == 422
    fault = r.json()["fault"]
    assert (fault["source"], fault["kind"], fault["model"]) == ("model-provider", "effort_unsupported", model)
    assert stack["sessions"].effort(MEMBER, "s1") == "" and stack["sessions"].model(MEMBER, "s1") == ""


def test_an_effort_with_no_model_is_checked_against_the_persons_default(stack):
    """`model: ""` follows the default (qwen3-32b: none or high); the effort is checked against it."""
    c = stack["client"]
    bad = c.post("/api/chat/model", json={"session": "s1", "model": "", "effort": "low"}, headers=_as(MEMBER))
    assert bad.status_code == 422 and bad.json()["fault"]["model"] == "qwen3-32b"
    ok = c.post("/api/chat/model", json={"session": "s1", "model": "", "effort": "high"}, headers=_as(MEMBER))
    assert ok.status_code == 200 and stack["sessions"].effort(MEMBER, "s1") == "high"


# ── the last pick is the person's default for new chats (founder 2026-10-13) ──────────────────

def _pick(c, who, session, model, effort=None):
    body = {"session": session, "model": model, **({"effort": effort} if effort else {})}
    return c.post("/api/chat/model", json=body, headers=_as(who))


def _listing(c, who, session):
    return c.get(f"/api/models/catalog?session={session}", headers=_as(who)).json()


def test_the_last_model_picked_is_the_default_of_a_new_chat(stack):
    c = stack["client"]
    assert _pick(c, MEMBER, "s1", "claude").status_code == 200
    fresh = _listing(c, MEMBER, "s2")
    assert (fresh["default"], fresh["selected"], fresh["default_effort"]) == ("claude", None, None)


def test_the_last_effort_picked_rides_with_the_model_into_a_new_chat(stack):
    c = stack["client"]
    _pick(c, MEMBER, "s1", "claude")
    assert _pick(c, MEMBER, "s1", "claude", "low").status_code == 200
    fresh = _listing(c, MEMBER, "s2")
    assert (fresh["default"], fresh["default_effort"]) == ("claude", "low")
    # the memory is the person's, on the server: another person's new chat is untouched
    assert _listing(c, ADMIN, "s2")["default"] == "qwen3-32b"


def test_a_new_chats_first_turn_runs_on_the_remembered_model_and_effort(stack):
    c = stack["client"]
    _pick(c, MEMBER, "s1", "claude", "low")
    assert _turn(c, MEMBER, "s2").status_code == 200
    env = stack["runtime"].envs[-1]
    assert (env["VEXA_AGENT_MODEL"], env["VEXA_AGENT_EFFORT"]) == ("claude-sonnet-4-6", "low")
    # the chat now holds what it ran on, so a later pick elsewhere does not move it
    assert (stack["sessions"].model(MEMBER, "s2"), stack["sessions"].effort(MEMBER, "s2")) == ("claude", "low")


def test_an_existing_chat_keeps_the_model_it_was_last_used_with(stack):
    c = stack["client"]
    assert _turn(c, MEMBER, "s0").status_code == 200              # runs on the catalog default
    assert stack["runtime"].envs[-1]["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B"
    _pick(c, MEMBER, "s1", "claude", "high")                       # the default moves
    assert _turn(c, MEMBER, "s0").status_code == 200
    assert stack["runtime"].envs[-1]["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B"
    assert _listing(c, MEMBER, "s0")["selected"] == "qwen3-32b"


def test_holding_the_model_a_chat_ran_on_costs_no_cold_start(stack):
    c = stack["client"]
    _turn(c, MEMBER, "s2")
    _turn(c, MEMBER, "s2")
    first, second = stack["runtime"].spawned[-2:]
    assert first == second


def test_switching_inside_a_chat_moves_that_chat_and_the_default(stack):
    c = stack["client"]
    _pick(c, MEMBER, "s1", "claude", "max")
    _pick(c, MEMBER, "s1", "qwen3-32b", "high")
    assert (_listing(c, MEMBER, "s1")["selected"], _listing(c, MEMBER, "s1")["selected_effort"]) == ("qwen3-32b", "high")
    fresh = _listing(c, MEMBER, "s9")
    assert (fresh["default"], fresh["default_effort"]) == ("qwen3-32b", "high")


def test_a_refused_pick_and_follow_my_default_leave_the_memory_alone(stack):
    c = stack["client"]
    _pick(c, MEMBER, "s1", "claude", "low")
    assert _pick(c, MEMBER, "s1", "or-sonnet").status_code == 403
    assert _pick(c, MEMBER, "s1", "claude", "none").status_code == 422
    assert _pick(c, MEMBER, "s1", "").status_code == 200
    fresh = _listing(c, MEMBER, "s2")
    assert (fresh["default"], fresh["default_effort"]) == ("claude", "low")


@pytest.mark.parametrize("model", ["or-sonnet", "retired-model"])
def test_a_remembered_model_no_longer_open_falls_back_silently(stack, model):
    """The catalog changed (or the entry closed) under the memory: the new chat shows the catalog's
    default and its first turn runs on it — never a refusal, never the closed model."""
    stack["sessions"].remember_pick(MEMBER, model, "low")
    c = stack["client"]
    fresh = _listing(c, MEMBER, "s2")
    assert (fresh["default"], fresh["default_effort"]) == ("qwen3-32b", None)
    assert _turn(c, MEMBER, "s2").status_code == 200
    assert stack["runtime"].envs[-1]["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B"


def test_a_remembered_model_falls_back_to_the_persons_settings_default_before_the_catalogs(stack):
    stack["model_config"].by_subject[MEMBER] = {"default_model": "claude"}
    stack["sessions"].remember_pick(MEMBER, "retired-model", "")
    assert _listing(stack["client"], MEMBER, "s2")["default"] == "claude"


def test_a_remembered_effort_the_model_does_not_offer_falls_back_to_its_own(stack):
    stack["sessions"].remember_pick(MEMBER, "qwen3-32b", "low")      # qwen offers none | high
    c = stack["client"]
    fresh = _listing(c, MEMBER, "s2")
    assert (fresh["default"], fresh["default_effort"]) == ("qwen3-32b", None)
    assert _turn(c, MEMBER, "s2").status_code == 200
    assert stack["sessions"].effort(MEMBER, "s2") == ""


def test_the_listing_with_a_remembered_effort_is_a_valid_model_list(stack):
    import contracts
    c = stack["client"]
    _pick(c, MEMBER, "s1", "claude", "low")
    contracts.validate_model_list(_listing(c, MEMBER, "s2"))
