"""Lite's runtime starts from a cleared environment, so no service secret of the instance reaches it
or any bot or worker it spawns as a child process.

Every Lite program inherits the entrypoint's exports, which carry the admin token, the internal tier,
the database password and the delegation and sign-in secrets. supervisord starts the runtime through
`bin/vexa-runtime`, which keeps only the keys the runtime's config.v1 declaration names plus host
plumbing.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "deploy" / "lite" / "bin" / "vexa-runtime"
RUNTIME_SRC = ROOT / "core" / "runtime" / "src"
SUPERVISORD = ROOT / "deploy" / "lite" / "supervisord.conf"
ENTRYPOINT = ROOT / "deploy" / "lite" / "entrypoint.sh"


def _launcher():
    loader = importlib.machinery.SourceFileLoader("vexa_runtime_launcher", str(LAUNCHER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _exports() -> dict[str, str]:
    """Every key the entrypoint exports, with a stand-in value."""
    keys = re.findall(r"^export ([A-Z][A-Z0-9_]*)=", ENTRYPOINT.read_text(), flags=re.M)
    return {k: f"value-of-{k}" for k in keys}


def test_no_instance_secret_survives_into_the_runtime():
    inherited = {**_exports(), "PATH": "/usr/bin", "HOME": "/root", "PYTHONPATH": str(RUNTIME_SRC)}
    env = _launcher().runtime_environment(inherited, str(RUNTIME_SRC))
    for secret in ("ADMIN_API_TOKEN", "INTERNAL_API_SECRET", "DB_PASSWORD", "VEXA_MCP_DELEGATION_SECRET",
                   "NEXTAUTH_SECRET", "JWT_SECRET", "MINIO_SECRET_KEY", "VEXA_SERVICE_AUTHORITY_SECRET",
                   "VEXA_SYSTEM_WEBHOOK_SECRET", "VEXA_MAIL_SMTP_PASSWORD", "VEXA_DISPATCH_SIGNING_KEY",
                   "REDIS_PASSWORD"):
        assert secret in inherited, f"the entrypoint no longer exports {secret}; update this test"
        assert secret not in env, f"{secret} reaches the runtime"


def test_the_runtime_keeps_what_it_declares_and_what_its_children_need():
    inherited = {**_exports(), "PATH": "/usr/bin", "HOME": "/root", "PYTHONPATH": str(RUNTIME_SRC),
                 "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright", "VEXA_HF_CACHE": "/opt/hf-cache"}
    env = _launcher().runtime_environment(inherited, str(RUNTIME_SRC))
    for key in ("RUNTIME_API_TOKEN", "BOT_COMMAND", "AGENT_WORKER_COMMAND", "ANTHROPIC_API_KEY",
                "BOT_SPEAKER_MIN_AUDIO_SEC", "PATH", "HOME", "PYTHONPATH", "PLAYWRIGHT_BROWSERS_PATH",
                "VEXA_HF_CACHE"):
        assert env.get(key) == inherited[key], key


def test_supervisord_starts_the_runtime_through_the_launcher():
    section = SUPERVISORD.read_text().split("[program:runtime]")[1].split("\n[")[0]
    command = re.search(r"^command=(.*)$", section, flags=re.M).group(1)
    assert command.endswith("/usr/local/bin/vexa-runtime")
    environment = re.search(r"^environment=(.*)$", section, flags=re.M).group(1)
    assert "INTERNAL_API_SECRET" not in environment
    assert 'RUNTIME_API_TOKEN="%(ENV_RUNTIME_API_TOKEN)s"' in environment


def test_only_the_runtime_and_its_two_callers_are_handed_the_caller_credential():
    text = SUPERVISORD.read_text()
    holders = sorted(name for name, body in re.findall(r"\[program:([a-z-]+)\]\n(.*?)(?=\n\[|\Z)", text, flags=re.S)
                     if "ENV_RUNTIME_API_TOKEN" in body)
    assert holders == ["agent-api", "meeting-api", "runtime"]
