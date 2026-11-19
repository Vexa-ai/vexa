"""The router v0 (`control_plane/model_router.py`, PLAN-8H 6.3, architecture decision G1).

Rules: a task type (chat, routines, events, home) → a model, inside the company's policy
(`models_allowed`). `model_rules` overlays the per-task keys that already exist (`model_routines`,
`model_event_agents`, `model_home`, `model_chat_default`), so an install that never sets it routes
exactly as before. Levels 1–5 (hosted plans, the `levels` feature, off by default) map to catalog ids:
`model_levels` when the company sets it, else the allowed models ordered by market price. A chat's
level is chosen once and kept; changing it on a chat that has one needs an explicit switch."""
from __future__ import annotations

import pytest

from control_plane import model_prices as mp
from control_plane import model_router as R

IDS = ["claude-opus-5-5", "qwen3.8-27b", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-fable-5-1"]


class _Catalog:
    empty = False

    def __init__(self, ids=IDS):
        self.ids = list(ids)
        self._by_id = {i: {"id": i, "model": i} for i in ids}


def _prices():
    return mp.PriceTable(None, env={mp.URL_ENV: "off"}, background=False)


# ── parsing ───────────────────────────────────────────────────────────────────────────────────
def test_a_flow_map_is_read_and_junk_is_dropped():
    assert R.parse_map("{routines: cheapest, chat: 'level:3', bogus}") == {"routines": "cheapest", "chat": "level:3"}
    assert R.parse_map("") == {} and R.parse_map(None) == {}


# ── levels ────────────────────────────────────────────────────────────────────────────────────
def test_default_levels_follow_market_price_cheapest_first():
    t = R.levels(_Catalog(), {}, _prices())
    assert t[1] == "claude-haiku-5-5"            # $0.10/$0.50
    assert t[5] == "claude-fable-5-1"            # $10/$50
    assert [t[n] for n in range(1, 6)] == ["claude-haiku-5-5", "qwen3.8-27b", "claude-sonnet-5-5",
                                           "claude-opus-5-5", "claude-fable-5-1"]


def test_levels_stay_inside_models_allowed_and_spread_over_fewer_models():
    t = R.levels(_Catalog(), {"models_allowed": "qwen3.8-27b, claude-sonnet-5-5"}, _prices())
    assert set(t.values()) == {"qwen3.8-27b", "claude-sonnet-5-5"}
    assert t[1] == "qwen3.8-27b" and t[5] == "claude-sonnet-5-5"


def test_the_company_table_wins_but_never_names_a_model_it_does_not_allow():
    pol = {"model_levels": "{1: qwen3.8-27b, 3: claude-sonnet-5-5, 5: claude-opus-5-5}",
           "models_allowed": "qwen3.8-27b, claude-sonnet-5-5"}
    t = R.levels(_Catalog(), pol, _prices())
    assert t[1] == "qwen3.8-27b" and t[3] == "claude-sonnet-5-5"
    assert t[5] in ("qwen3.8-27b", "claude-sonnet-5-5")        # opus is not allowed: the price order fills it


def test_no_catalog_no_levels():
    class Empty:
        empty = True
        ids: list = []
    assert R.levels(Empty(), {}, _prices()) == {}


def test_level_of_is_the_inverse():
    t = R.levels(_Catalog(), {}, _prices())
    assert R.level_of(t, "claude-sonnet-5-5") == 3 and R.level_of(t, "nope") is None


# ── rules ─────────────────────────────────────────────────────────────────────────────────────
def test_without_model_rules_the_existing_keys_route_as_before():
    cat = _Catalog()
    assert R.for_task(cat, "routines", {"model_routines": "claude-sonnet-5-5"}, _prices()) == "claude-sonnet-5-5"
    assert R.for_task(cat, "events", {"model_event_agents": "cheapest"}, _prices()) == "claude-haiku-5-5"


def test_model_rules_overlay_and_may_name_a_level():
    cat = _Catalog()
    pol = {"model_routines": "claude-sonnet-5-5", "model_rules": "{routines: 'level:2', events: claude-opus-5-5}"}
    assert R.for_task(cat, "routines", pol, _prices()) == "qwen3.8-27b"
    assert R.for_task(cat, "events", pol, _prices()) == "claude-opus-5-5"


def test_a_rule_naming_a_model_outside_the_policy_routes_to_nothing_never_around_it():
    pol = {"model_rules": "{routines: claude-opus-5-5}", "models_allowed": "qwen3.8-27b"}
    assert R.for_task(_Catalog(), "routines", pol, _prices()) == ""


def test_cheapest_is_by_market_price_within_the_policy():
    pol = {"model_rules": "{home: cheapest}", "models_allowed": "claude-sonnet-5-5, qwen3.8-27b"}
    assert R.for_task(_Catalog(), "home", pol, _prices()) == "qwen3.8-27b"


def test_background_dispatch_routes_through_the_router():
    from control_plane import dispatch
    cat = _Catalog()
    cat.policy = lambda: {"model_rules": "{routines: 'level:5'}", "models_allowed": "all"}
    assert dispatch.background_model(cat, "agent-u1-chat-main-auto", "scheduled") == "claude-fable-5-1"


# ── L2: the levels API (feature `levels`, off by default) ─────────────────────────────────────
import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from control_plane.api import _Sessions, create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.model_providers import parse  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared.config import load_settings  # noqa: E402
from tests.model_catalogs import ENV, EXAMPLE  # noqa: E402


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


def _stack(tmp_path, monkeypatch, features):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-key")
    monkeypatch.setenv("VEXA_FEATURES", features)
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text("---\nmodels_allowed: qwen3-32b, claude\n---\n# Policies\n")
    dispatcher = Dispatcher(load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global")),
                            _Runtime(), _Identity(), catalog=parse(json.dumps(EXAMPLE), ENV))
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)), sessions=_Sessions())
    return TestClient(app)


H = {"X-User-Id": "u_member"}


def test_levels_are_refused_with_the_feature_off(tmp_path, monkeypatch):
    c = _stack(tmp_path, monkeypatch, "")
    r = c.get("/api/models/levels", headers=H)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "feature_disabled"
    assert c.post("/api/chat/level", headers=H, json={"session": "s1", "level": 2}).status_code == 403


def test_a_chat_takes_a_level_once_and_a_change_needs_an_explicit_switch(tmp_path, monkeypatch):
    c = _stack(tmp_path, monkeypatch, "levels")
    t = c.get("/api/models/levels", headers=H).json()
    assert [row["level"] for row in t["levels"]] == [1, 2, 3, 4, 5]
    assert t["levels"][0]["word"] == "fastest" and t["levels"][4]["word"] == "most capable"
    assert {row["model"] for row in t["levels"]} == {"qwen3-32b", "claude"}     # inside models_allowed
    top = t["levels"][4]["model"]
    ok = c.post("/api/chat/level", headers=H, json={"session": "s1", "level": 5})
    assert ok.status_code == 200 and ok.json()["model"] == top and ok.json()["level"] == 5
    assert c.get("/api/models/levels?session=s1", headers=H).json()["chat"] == {"level": 5, "model": top}
    fixed = c.post("/api/chat/level", headers=H, json={"session": "s1", "level": 1})
    assert fixed.status_code == 409 and fixed.json()["detail"]["reason"] == "level_fixed"
    sw = c.post("/api/chat/level", headers=H, json={"session": "s1", "level": 1, "switch": True})
    assert sw.status_code == 200 and sw.json()["level"] == 1
    assert c.post("/api/chat/level", headers=H, json={"session": "s1", "level": 9}).status_code == 422


def test_levels_are_not_in_all_so_app_dev_keeps_the_picker():
    from control_plane import features as F
    assert F.install_defaults({F.ENV: "all"})["levels"] is False


# ── no `model_rules`: exactly today's choice (base 607025f34's background_model) ─────────────
def _base_background_model(catalog, unit_id, pol):
    """base's code, verbatim in effect: the per-task key, else `cheapest` = home_model.pick."""
    from control_plane import budgets, home_model
    if not pol:
        return ""
    key = "model_routines" if unit_id.endswith("-auto") else "model_event_agents"
    want = str(pol.get(key) or "cheapest").strip()
    allowed = budgets.allowed_list(pol)
    if want != "cheapest":
        return want if want in catalog.ids and (allowed is None or want in allowed) else ""
    return home_model.pick(catalog, {}, named="", allowed=allowed)


@pytest.mark.parametrize("ids", [
    IDS,
    # market-cheapest (qwen, $0.24) is NOT what base picks (the first "small"-named model, gpt-mini)
    ["qwen3.8-27b", "openai/gpt-5-mini", "claude-sonnet-5-5"],
    ["claude-sonnet-5-5", "my-lab/finetune-7b"],
])
@pytest.mark.parametrize("pol", [
    {}, {"models_allowed": "all"}, {"model_routines": "claude-sonnet-5-5"}, {"model_event_agents": "qwen3.8-27b"},
    {"models_allowed": "qwen3.8-27b, openai/gpt-5-mini"}, {"model_routines": "not-in-catalog"},
    {"model_routines": "claude-sonnet-5-5", "models_allowed": "qwen3.8-27b"},
])
@pytest.mark.parametrize("unit", ["agent-u1-chat-main-auto", "agent-u1-event-0123456789"])
def test_without_model_rules_the_choice_is_identical_to_base(ids, pol, unit):
    from control_plane import dispatch
    cat = _Catalog(ids)
    cat.policy = lambda: dict(pol)
    trigger = "scheduled" if unit.endswith("-auto") else "event"
    assert dispatch.background_model(cat, unit, trigger) == _base_background_model(cat, unit, pol)
