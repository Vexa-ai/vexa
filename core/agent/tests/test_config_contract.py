"""config.v1 (ADR-0026) — agent-api's declaration, the pydantic-settings ↔ declaration sync (every
``Settings`` field's VEXA_* env name must be declared — the Python-side half of what
gate:config-contract's regex scanner cannot introspect), the capability tri-states (bot_gateway ·
model_inference), and the ADDITIVE /health rows next to the existing dispatcher check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import config_preflight as cp
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import Settings, load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "fake-token"


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    cp._reset_probe_cache()
    yield
    cp._reset_probe_cache()


def test_declaration_loads_and_is_internally_consistent():
    decl = cp.load_declaration()
    assert decl["service"] == "agent-api"
    assert set(decl["capabilities"]) == {"bot_gateway", "model_inference", "connections",
                                         "git_credential_broker", "worker_toolbelt"}
    assert decl["capabilities"]["worker_toolbelt"]["mode"] == "all"
    assert decl["capabilities"]["model_inference"]["mode"] == "any"


def _env_names(field_name, field):
    """Every env var this Settings field actually reads — the VEXA_ prefix by default, or the
    explicit `validation_alias` choices where a field carries one (F95: `internal_api_secret` reads
    the canonical `INTERNAL_API_SECRET` first and the prefixed spelling as a deprecated fallback)."""
    alias = getattr(field, "validation_alias", None)
    choices = getattr(alias, "choices", None)
    if choices:
        return [str(c) for c in choices]
    if isinstance(alias, str):
        return [alias]
    return [f"VEXA_{field_name.upper()}"]


def test_every_settings_field_is_declared():
    """pydantic-settings reads env by field name (VEXA_ prefix) — invisible to the gate's literal
    os.getenv scanner, so THIS test holds the sync: a new Settings field must land in the
    declaration (the SSOT) to pass. A field carrying an explicit alias must have EVERY spelling it
    accepts declared, or one name of a secret drifts out of the contract while the other stays in —
    which is precisely the shape F95 arrived in."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    for field_name, field in Settings.model_fields.items():
        for env_name in _env_names(field_name, field):
            assert env_name in declared, (
                f"Settings.{field_name} reads {env_name} but config.v1.json does not declare it — "
                "add it to core/agent/control_plane/config.v1.json"
            )


def test_internal_secret_has_one_canonical_name_and_a_deprecated_alias(monkeypatch):
    """F95 — one secret had three names, and each name grew its own refusal list.

    The canonical name is the compose/helm secret KEY, `INTERNAL_API_SECRET`, the same name
    admin-api, gateway and meeting-api read. The prefixed spelling still resolves so an operator
    mid-upgrade is warned rather than silently dropped into an unauthenticated internal tier — but
    it must never WIN over the canonical one, or a stale export quietly shadows the real value."""
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "deprecated")
    assert load_settings().internal_api_secret.get_secret_value() == "deprecated"

    monkeypatch.setenv("INTERNAL_API_SECRET", "canonical")
    assert load_settings().internal_api_secret.get_secret_value() == "canonical"

    monkeypatch.delenv("VEXA_INTERNAL_API_SECRET", raising=False)
    assert load_settings().internal_api_secret.get_secret_value() == "canonical"

    # Constructing by FIELD name still works — every other test in this tree builds Settings that
    # way, and an alias that broke it would have been a silent test-only regression.
    assert load_settings(internal_api_secret="explicit").internal_api_secret\
        .get_secret_value() == "explicit"


def test_preflight_refuses_a_secretless_or_placeheld_internal_tier():
    """agent-api both PRESENTS the internal secret and BELIEVES it — `_internal_caller` compares
    this value, and the meeting room's gate 0 is by that code's own statement the trust boundary on
    who is in the room. Unset used to mean `_internal_caller` simply returned False, which is a
    half-configured tier nobody can see; a PUBLISHED placeholder is worse, because it is that tier
    handed to every reader of the repository (F95)."""
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({})
    assert "INTERNAL_API_SECRET" in str(ei.value)
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme"):
        with pytest.raises(cp.ConfigError) as ei:
            cp.preflight({"INTERNAL_API_SECRET": placeholder, **IDENTITY})
        assert "INTERNAL_API_SECRET" in str(ei.value)
        assert placeholder not in str(ei.value), "a refusal must never echo the value"
    cp.preflight({"INTERNAL_API_SECRET": "a-real-secret", **IDENTITY})


RUNTIME_TOKEN = "runtime-caller-token-for-tests-0123456789abcdef"
DISPATCH_KEY = "dispatch-signing-key-for-tests-0123456789abcdef"
IDENTITY = {"VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": "/run/vexa-identity/public/key.pem",
            "RUNTIME_API_TOKEN": RUNTIME_TOKEN, "VEXA_DISPATCH_SIGNING_KEY": DISPATCH_KEY}


def test_preflight_refuses_a_boot_that_cannot_reach_the_runtime():
    """Every worker spawn and routine job presents the runtime caller credential; the runtime refuses
    any other caller, so a boot without one (or with a published placeholder) refuses."""
    base = {"INTERNAL_API_SECRET": "a-real-secret",
            "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": "/run/vexa-identity/public/key.pem",
            "VEXA_DISPATCH_SIGNING_KEY": DISPATCH_KEY}
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight(base)
    assert "RUNTIME_API_TOKEN" in str(ei.value)
    with pytest.raises(cp.ConfigError):
        cp.preflight({**base, "RUNTIME_API_TOKEN": "changeme"})
    cp.preflight({**base, "RUNTIME_API_TOKEN": RUNTIME_TOKEN})


def test_preflight_refuses_an_unset_or_published_dispatch_signing_key():
    """agent-api signs every dispatch's identity token with VEXA_DISPATCH_SIGNING_KEY. Its default,
    `dev-dispatch-signing-key`, was published in this repository and shipped on every deploy
    surface, so a token signed with it proved nothing. The boot refuses it, every internal-secret
    placeholder, and an unset key, and never echoes the value."""
    base = {"INTERNAL_API_SECRET": "a-real-secret", **IDENTITY}
    unset = {k: v for k, v in base.items() if k != "VEXA_DISPATCH_SIGNING_KEY"}
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight(unset)
    assert "VEXA_DISPATCH_SIGNING_KEY" in str(ei.value)
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({**unset, "VEXA_DISPATCH_SIGNING_KEY": "   "})
    assert "VEXA_DISPATCH_SIGNING_KEY" in str(ei.value)
    forbidden = next(k["forbidden_values"] for k in cp.load_declaration()["keys"]
                     if k["key"] == "VEXA_DISPATCH_SIGNING_KEY")
    assert "dev-dispatch-signing-key" in forbidden
    for published in forbidden:
        with pytest.raises(cp.ConfigError) as ei:
            cp.preflight({**base, "VEXA_DISPATCH_SIGNING_KEY": published})
        assert "VEXA_DISPATCH_SIGNING_KEY" in str(ei.value)
        if "-" in published:   # "secret" and "default" are ordinary words in the message itself
            assert published not in str(ei.value), "a refusal must never echo the value"
    cp.preflight(base)


def test_the_dispatch_token_is_never_signed_with_an_empty_key():
    """The code default is empty now; an empty key must not sign anything, since such a token
    verifies for anyone who knows the format."""
    from shared.adapters import LocalIdentityMinter

    assert Settings().dispatch_signing_key.get_secret_value() == ""
    with pytest.raises(ValueError):
        LocalIdentityMinter("")


def test_preflight_refuses_a_boot_that_cannot_verify_identity():
    """gateway-identity.v1 — agent-api believes an x-user-* header only with the gateway's signature
    beside it; with no key to check it, nobody can be authenticated, so the boot refuses."""
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})
    assert "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE" in str(ei.value)
    with pytest.raises(cp.ConfigError):
        cp.preflight({"INTERNAL_API_SECRET": "a-real-secret", "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": ""})


def test_the_toolbelt_is_a_capability_with_both_halves():
    assert cp.capability_states({})["worker_toolbelt"] == cp.NOT_CONFIGURED
    both = {"VEXA_MCP_URL": "http://gateway:8000/mcp", "VEXA_MCP_DELEGATION_SECRET": "k"}
    assert cp.capability_states(both)["worker_toolbelt"] == cp.CONFIGURED
    assert cp.capability_states({"VEXA_MCP_URL": "http://gateway:8000/mcp"})["worker_toolbelt"] \
        == cp.MISCONFIGURED


def test_capability_tri_states():
    assert cp.capability_states({})["bot_gateway"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"VEXA_BOT_API_KEY": "k"})["bot_gateway"] == cp.CONFIGURED
    # mode=any: any ONE model-credential path configures the agent plane's model_inference row
    assert cp.capability_states({})["model_inference"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"HOST_CLAUDE_CREDENTIALS": "/x.json"})["model_inference"] == cp.CONFIGURED
    assert cp.capability_states({"ANTHROPIC_AUTH_TOKEN": "tok"})["model_inference"] == cp.CONFIGURED


def test_preflight_reports_capability_rows(monkeypatch):
    """agent-api used to have NO required-explicit key, and this test was named for that fact. F95
    gave it one — INTERNAL_API_SECRET, which it both presents and believes — so the environment now
    has to carry it before the capability rows can be reached at all."""
    for k in ("VEXA_BOT_API_KEY", "HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-secret")
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", "/run/vexa-identity/public/key.pem")
    monkeypatch.setenv("RUNTIME_API_TOKEN", RUNTIME_TOKEN)
    monkeypatch.setenv("VEXA_DISPATCH_SIGNING_KEY", DISPATCH_KEY)
    report = cp.preflight()
    assert report["service"] == "agent-api"
    assert report["capabilities"]["bot_gateway"]["state"] == cp.NOT_CONFIGURED
    assert report["capabilities"]["model_inference"]["state"] == cp.NOT_CONFIGURED


def test_health_carries_capability_rows_additively(monkeypatch):
    for k in ("VEXA_BOT_API_KEY", "HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()))
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    # the pre-existing consumers' keys are untouched
    assert body["status"] == "ok"
    assert body["service"] == "agent-api"
    assert body["checks"]["dispatcher"] is True
    # the additive config.v1 rows; unconfigured capabilities NEVER degrade status
    assert body["capabilities"]["bot_gateway"]["state"] == cp.NOT_CONFIGURED
    assert body["capabilities"]["model_inference"]["state"] == cp.NOT_CONFIGURED


def test_health_degraded_path_still_carries_rows():
    # the dispatcher-absent 503 (P18) keeps its shape AND gains the rows
    r = TestClient(create_app(None)).get("/health")  # type: ignore[arg-type]
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "capabilities" in body


# ── the WORKER and the HARNESS are part of this service's config surface (F91 · F93) ─────────────

_ENV_READ = re.compile(r"""os\.(?:getenv|environ\.get|environ\.setdefault)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
                       r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]""")

#: Process plumbing a module may read without a declaration — the same tight list gate:config-contract
#: keeps (CONFIG_SURFACE_ALLOW). Interpreter/runtime wiring only, never product config.
_PLUMBING = {"PYTHONUNBUFFERED", "PYTHONPATH", "DISPLAY", "NODE_ENV", "HOSTNAME", "TZ", "PGTZ",
             "HOME", "TMPDIR"}


def _env_reads(*dirs: str) -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    found: dict[str, str] = {}
    for d in dirs:
        for path in sorted((root / d).rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            for m in _ENV_READ.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(m.group(1) or m.group(2), str(path.relative_to(root)))
    return found


def test_every_env_read_in_the_harness_and_the_worker_is_declared():
    """F91. gate:config-contract scanned only `control_plane` + `shared`, so every dial the TURN
    actually runs on was invisible to the contract — `VEXA_LLM_EXTRA_BODY`, whose absence fails
    silently (the turn runs with thinking on), was declared nowhere at all. agent-api does not read
    these itself; it STAMPS them into every worker spec env, which is exactly why they are its
    config surface. This is the Python-side twin of the widened gate scan: either alone can be
    edited away, both cannot be by accident."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    for key, where in sorted(_env_reads("llm", "worker").items()):
        assert key in declared or key in _PLUMBING, (
            f"{where} reads {key} but config.v1.json does not declare it — add it to "
            "core/agent/control_plane/config.v1.json"
        )


def test_the_qwen_lane_dials_are_declared():
    """Named one by one because these are the keys whose absence is SILENT: a mis-stamped
    extra_body leaves thinking on and the turn merely returns nothing parseable."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    assert {"VEXA_LLM_BASE_URL", "VEXA_LLM_API_KEY", "VEXA_LLM_MODEL", "VEXA_LLM_EXTRA_BODY",
            "VEXA_AGENT_MODEL", "VEXA_AGENT_STREAM", "VEXA_AGENT_MAX_TOOL_CALLS",
            "VEXA_AGENT_MAX_TURN_SEC", "VEXA_AGENT_CONTEXT_TOKENS", "VEXA_MOUNTS",
            "VEXA_RUNNER"} <= declared


#: The number gate:config-contract PRINTS. It used to be compared to nothing, so it could move by
#: any amount — a key silently dropped from the declaration reads as a smaller, equally green line
#: (F93). Bump this deliberately, in the same commit that adds or removes a key.
# 84 on `harness-hardening` (which declared the 22 llm/worker keys), plus the two the line
# gained while that branch was open: INTERNAL_API_SECRET (the security hotfix, PR #1424) and
# VEXA_BUILD_SHA (the version bar, PR #1422). The union is duplicate-free and drops nothing
# from either side.
# +1 on `flows-delivery`: VEXA_ROOM_MEETING, the post-meeting room signal the worker reads to
# keep decision 22 (no desk write-back, no README refresh, no bot verbs on a finished meeting —
# F103/F104). The widened scan this file's sibling test performs is what caught it undeclared.
# 87 since VEXA_ENV was declared beside VEXA_SECRETS_KEY (R-E08): the profile word decides whether
# an unset store key is a generated one or a boot refusal, so the two are read together and are
# declared together.
# 90: +1 VEXA_UNIT_ID, #1510 (fallback_session reads it).
# +3: VEXA_SEARCH_URL / VEXA_SEARCH_DIALECT / VEXA_SEARCH_API_KEY — the openai-agent harness's
# WebSearch adapter. The endpoint is the OPERATOR'S (no search engine ships with Vexa), so all
# three are deployment configuration this service stamps into every worker.
# 94: +1 VEXA_TARGET_WORKSPACE (Vexa-ai/vexa#1611) — the chat's target workspace, the one mount its
# writes go to. The worker reads it to MARK that mount in its own stack declaration, so a write with
# no better instruction has a path rather than a guess.
# 98: +4 VEXA_AGENT_MAX_TOOL_CALLS_{CHAT,JOB,ROOM,FLOW} (Vexa-ai/vexa#1622) — the per-kind budget
# table. Declared BY HAND, and that is the finding: `_env_reads` cannot see them, because the
# harness reads every budget through `_int_env(name, default)` and the scan looks for `os.environ`
# with a literal beside it. The same blind spot already hides #1613's VEXA_AGENT_JOB_MAX_TOOL_CALLS
# and VEXA_AGENT_JOB_MAX_TURN_SEC, which are read by the shipped worker and declared nowhere.
# 98 at v0.13.1; 103 in v0.13.2: VEXA_AGENT_AUTO_CONTINUE_CHAT, and the four Connections keys —
# VEXA_CONNECTIONS_BROKER_URL + VEXA_CONNECTIONS_AGENT_KEY_FILE (capability `connections`) and
# VEXA_GIT_STORE_BROKER_URL + VEXA_GIT_STORE_KEY_FILE (capability `git_credential_broker`). The four
# were first declared `targets: []` for a dogfood overlay; since the broker is a product service
# (ADR-0040) they are plumbed on compose and helm, and gate:config-contract holds them there.
# 104: +1 VEXA_AGENT_MAX_CHAT_CONTINUATIONS — the bound on VEXA_AGENT_AUTO_CONTINUE_CHAT, and both
# now plumbed on compose, helm and lite (they were `targets: []`, so no standard install could set them).
# In the same span VEXA_REQUIRE_GATEWAY_IDENTITY was retired for VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE
# (one key out, one in, net 0): agent-api believes an x-user-* header only when the gateway signed it.
# 105: +1 RUNTIME_API_TOKEN — the runtime caller credential every runtime.v1 / schedule.v1 call presents.
# 106: +1 REDIS_WORKLOAD_ACL — what a spawned worker connects to Redis as.
# 107: +1 CODEX_HOME — the worker's Codex home, named by the runtime and read by the harness.
# 106: -1 VEXA_WORKSPACE_MOUNT_SOURCE — the store backing is the runtime's own configuration (it drops
# the key from every spec), so agent-api no longer reads, stamps or declares it; compose and helm
# set it on the runtime only.
# 105: -1 VEXA_AGENT_DEFAULT_SUBJECT — a test-harness fallback subject; the harness now passes its
# subject to create_app and the product reads none.
# 106: +1 VEXA_UNIT_IN_KEY — the unit's input-stream key; the worker runs only entries signed with it.
# 107: +1 VEXA_MODEL_CATALOG — the operator's model catalog (ADR-0042, models.v1 Catalog).
EXPECTED_DECLARED_KEYS = 107


def test_connections_keys_are_capabilities_on_real_surfaces():
    """The broker keys are optional (the no-Connections deployment is a real one) and plumbed —
    never a `targets: []` dial a standard install cannot set."""
    decl = cp.load_declaration()
    keys = {k["key"]: k for k in decl["keys"]}
    for key, cap in [("VEXA_CONNECTIONS_BROKER_URL", "connections"), ("VEXA_CONNECTIONS_AGENT_KEY_FILE", "connections"),
                     ("VEXA_GIT_STORE_BROKER_URL", "git_credential_broker"), ("VEXA_GIT_STORE_KEY_FILE", "git_credential_broker")]:
        assert keys[key]["class"] == "capability" and keys[key]["capability"] == cap
        assert keys[key]["targets"] == ["compose", "helm"], key
    assert decl["capabilities"]["connections"]["when_unconfigured"]


def test_the_declared_key_count_is_asserted_not_merely_printed():
    decl = cp.load_declaration()
    assert len(decl["keys"]) == EXPECTED_DECLARED_KEYS, (
        f"agent-api declares {len(decl['keys'])} keys, this tripwire expects "
        f"{EXPECTED_DECLARED_KEYS}. If the change is intended, update EXPECTED_DECLARED_KEYS here "
        "in the same commit — the gate prints this number and comparing it to nothing is how a "
        "dropped declaration stays green."
    )
    assert len({k["key"] for k in decl["keys"]}) == len(decl["keys"]), "a key is declared twice"
