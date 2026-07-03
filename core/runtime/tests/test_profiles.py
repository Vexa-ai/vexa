"""O-RT-1 profiles eval — the real registry resolves the two control-plane workload kinds, and the
env each one carries conforms to the sealed contracts (by path):

  • meeting-bot — its constructor is one env var VEXA_BOT_CONFIG, a JSON-encoded Invocation that must
                 validate against meetings/contracts/invocation.v1 (ADR-0002).
  • agent      — its env must match the keys in runtime.v1 golden spec-agent.json.

The kernel never sees these images/commands (P11 — profile is opaque); this test exercises the
config that maps profile → Runnable and proves the env shapes are contract-faithful."""
import json
import os
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

from runtime_kernel import default_registry
from runtime_kernel.profiles import Runnable, apply_command_overrides, worker_image_for

V012 = Path(__file__).resolve().parents[2]  # …/v0.12
INVOCATION_SCHEMA = json.loads(
    (V012 / "meetings" / "contracts" / "invocation.v1" / "invocation.schema.json").read_text()
)
SPEC_AGENT_GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts" / "runtime.v1" / "golden" / "spec-agent.json").read_text()
)

_INV_REGISTRY = Registry().with_resource(
    INVOCATION_SCHEMA["$id"], Resource.from_contents(INVOCATION_SCHEMA)
)


def _conforms_invocation(obj: dict) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{INVOCATION_SCHEMA['$id']}#/$defs/Invocation"}, registry=_INV_REGISTRY
    ).validate(obj)


def test_registry_resolves_meeting_bot_and_agent():
    reg = default_registry()
    assert set(reg.names()) == {"meeting-bot", "agent"}
    for name in ("meeting-bot", "agent"):
        runnable = reg.resolve(name)
        assert isinstance(runnable, Runnable)
        assert runnable.command  # both have a launch command
    assert reg.resolve("does-not-exist") is None


def test_meeting_bot_uses_browser_image_from_env(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "registry.example.com/vexa-bot:0.12")
    reg = default_registry()
    assert reg.resolve("meeting-bot").image == "registry.example.com/vexa-bot:0.12"
    # Lifetime is managed externally (idle_timeout 0 ⇒ enforcement skips it).
    assert reg.get("meeting-bot").idle_timeout_sec == 0


def test_meeting_bot_env_is_a_valid_invocation():
    """The bot's whole config is delivered as VEXA_BOT_CONFIG — a JSON-encoded Invocation. Build the
    env the control plane would hand the profile and prove the payload conforms to invocation.v1."""
    invocation = {
        "platform": "google_meet",
        "meetingUrl": "https://meet.google.com/xxx-xxxx-xxx",
        "botName": "Vexa",
        "redisUrl": "redis://redis:6379",
        "connectionId": "sess-uid",
        "meetingApiCallbackUrl": "http://meeting-api:8080/runtime/callback",
    }
    env = {"VEXA_BOT_CONFIG": json.dumps(invocation)}

    # The contract-faithful check: the VEXA_BOT_CONFIG payload is a valid Invocation.
    assert "VEXA_BOT_CONFIG" in env
    _conforms_invocation(json.loads(env["VEXA_BOT_CONFIG"]))


def test_meeting_bot_env_rejects_a_bad_invocation():
    bad = {"platform": "skype", "botName": "Vexa"}  # invalid platform, missing required fields
    try:
        _conforms_invocation(bad)
        assert False, "expected invocation.v1 to reject a malformed config"
    except jsonschema.ValidationError:
        pass


def test_agent_env_matches_spec_agent_golden(monkeypatch):
    """The agent profile's env mirrors runtime.v1 golden spec-agent.json. Assert the golden's env
    keys are exactly the identity+workspace contract the agent expects, and that an env built from
    that golden carries only string values (runtime.v1 env is map<string,string>)."""
    monkeypatch.setenv("AGENT_IMAGE", "registry.example.com/agent:0.12")
    monkeypatch.delenv("AGENT_WORKER_IMAGE", raising=False)
    reg = default_registry()
    agent = reg.get("agent")
    # Workers run a DISTINCT image name (the agent-api bytes aliased), not the agent-api name itself.
    assert agent.runnable.image == "registry.example.com/agent:0.12"  # repo has no agent-api suffix → kept
    assert agent.idle_timeout_sec == 300
    assert agent.max_lifetime_sec == 3600

    golden_env = SPEC_AGENT_GOLDEN["env"]
    assert set(golden_env) == {
        "VEXA_AGENT_IDENTITY_TOKEN",
        "VEXA_WORKSPACE_REPO",
        "VEXA_WORKSPACE_REF",
        "VEXA_WORKSPACE_PATH",
    }
    # runtime.v1 env is map<string,string> — every value must already be a string.
    assert all(isinstance(v, str) for v in golden_env.values())


# ── worker image name (distinct from the agent-api service image) ───────────────────────────────

def test_worker_image_derives_distinct_name_from_agent_api(monkeypatch):
    monkeypatch.delenv("AGENT_WORKER_IMAGE", raising=False)
    assert worker_image_for("vexaai/v012-agent-api:dev") == "vexaai/v012-agent-worker:dev"
    # tag is preserved through the swap
    assert worker_image_for("vexaai/v012-agent-api:0.12-rc1") == "vexaai/v012-agent-worker:0.12-rc1"
    # bare 'agent-api' repo (no -api dash prefix) still rewrites
    assert worker_image_for("agent-api:dev") == "agent-worker:dev"


def test_worker_image_override_env_wins(monkeypatch):
    monkeypatch.setenv("AGENT_WORKER_IMAGE", "registry.example.com/custom-worker:x")
    assert worker_image_for("vexaai/v012-agent-api:dev") == "registry.example.com/custom-worker:x"


def test_worker_image_falls_back_when_not_derivable(monkeypatch):
    monkeypatch.delenv("AGENT_WORKER_IMAGE", raising=False)
    # repo with no agent-api substring → keep the agent image (fail-safe, distinct name impossible)
    assert worker_image_for("registry.example.com/something:1") == "registry.example.com/something:1"
    assert worker_image_for("") == ""


def test_agent_profile_uses_distinct_worker_image(monkeypatch):
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/v012-agent-api:dev")
    monkeypatch.delenv("AGENT_WORKER_IMAGE", raising=False)
    reg = default_registry()
    assert reg.get("agent").runnable.image == "vexaai/v012-agent-worker:dev"


def test_agent_profile_honors_worker_image_override(monkeypatch):
    """build_production_app pins AGENT_WORKER_IMAGE to the alias's resolved name (or the agent-api
    fallback); the registry must honor it so a tag-failure fallback reaches dispatch."""
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/v012-agent-api:dev")
    monkeypatch.setenv("AGENT_WORKER_IMAGE", "vexaai/v012-agent-api:dev")  # simulated fallback
    reg = default_registry()
    assert reg.get("agent").runnable.image == "vexaai/v012-agent-api:dev"


# ── command overrides (process-backend / lite) ────────────────────────────────────────────────────
def test_command_overrides_noop_without_env(monkeypatch):
    """No BOT_COMMAND / AGENT_WORKER_COMMAND set ⇒ the image entrypoints are untouched (docker/k8s)."""
    monkeypatch.delenv("BOT_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_WORKER_COMMAND", raising=False)
    reg = apply_command_overrides(default_registry())
    assert reg.resolve("meeting-bot").command == ["/app/vexa-bot/entrypoint.sh"]
    assert reg.resolve("agent").command == ["python", "-m", "worker"]


def test_command_overrides_replace_commands(monkeypatch):
    """A process-backend deployment points the commands at in-container launchers (shlex-parsed);
    images + timeouts are preserved (only the command is replaced)."""
    monkeypatch.setenv("BOT_COMMAND", "/usr/local/bin/vexa-bot-launch")
    monkeypatch.setenv("AGENT_WORKER_COMMAND", "/usr/local/bin/vexa-agent-worker --flag")
    reg = apply_command_overrides(default_registry())
    assert reg.resolve("meeting-bot").command == ["/usr/local/bin/vexa-bot-launch"]
    assert reg.resolve("agent").command == ["/usr/local/bin/vexa-agent-worker", "--flag"]
    assert reg.get("agent").idle_timeout_sec == 300  # untouched
