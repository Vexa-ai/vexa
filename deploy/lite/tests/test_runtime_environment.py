"""What each Lite program's environment holds.

Every Lite program inherits supervisord's environment — the entrypoint's exports, which carry the
admin token, the internal tier, the database password and the delegation and sign-in secrets — plus
its own ``environment=``. So:

* the runtime starts from a cleared environment (``bin/vexa-runtime`` keeps only the keys the
  runtime's config.v1 declaration names plus host plumbing), and no service secret reaches it or any
  bot or worker it spawns;
* the runtime caller credential is not exported at all: the entrypoint renders it into the
  ``environment=`` of the runtime, agent-api and meeting-api only, in a root-only copy of the
  supervisor config (``bin/render-supervisord``) that supervisord runs.

These tests compute each program's effective environment from the rendered config. The live
counterpart, ``program_environments.py``, reads it from ``/proc`` inside a booted container.
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
DOCKERFILE = ROOT / "deploy" / "lite" / "Dockerfile.lite"
RENDERER = ROOT / "deploy" / "lite" / "bin" / "render-supervisord"
TOKEN = "t" * 64
HOLDERS = {"runtime", "agent-api", "meeting-api"}


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _launcher():
    return _load(LAUNCHER, "vexa_runtime_launcher")


def _image_env() -> dict[str, str]:
    """The keys Dockerfile.lite's ENV instructions set."""
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    keys: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("ENV "):
            keys.update({k: v for k, v in re.findall(r"([A-Z][A-Z0-9_]*)=(\S*)", line)})
    return keys


def _exports() -> dict[str, str]:
    """Every key the entrypoint exports, with a stand-in value."""
    keys = re.findall(r"^\s*export ([A-Z][A-Z0-9_]*)=", ENTRYPOINT.read_text(), flags=re.M)
    return {k: f"value-of-{k}" for k in keys}


def _supervisord_env() -> dict[str, str]:
    """supervisord's own environment: the image ENV, then the entrypoint's exports."""
    return {**_image_env(), **_exports()}


def _rendered_programs(token: str = TOKEN) -> dict[str, tuple[str, dict[str, str]]]:
    """program → (command, effective environment) from the config the entrypoint renders."""
    text = _load(RENDERER, "render_supervisord").render(SUPERVISORD.read_text(), token)
    parent = _supervisord_env()
    out = {}
    for name, body in re.findall(r"^\[program:([a-z-]+)\]\n(.*?)(?=^\[|\Z)", text, flags=re.S | re.M):
        command = re.search(r"^command=(.*)$", body, flags=re.M).group(1)
        line = re.search(r"^environment=(.*)$", body, flags=re.M)
        own = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"', line.group(1))) if line else {}
        own = {k: re.sub(r"%\(ENV_([A-Z0-9_]+)\)s", lambda m: parent[m.group(1)], v) for k, v in own.items()}
        out[name] = (command, {**parent, **own})
    return out


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
    _command, inherited = _rendered_programs()["runtime"]
    inherited = {**inherited, "PATH": "/usr/bin", "HOME": "/root", "PYTHONPATH": str(RUNTIME_SRC),
                 "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright", "VEXA_HF_CACHE": "/opt/hf-cache"}
    env = _launcher().runtime_environment(inherited, str(RUNTIME_SRC))
    for key in ("RUNTIME_API_TOKEN", "BOT_COMMAND", "AGENT_WORKER_COMMAND", "ANTHROPIC_API_KEY",
                "BOT_SPEAKER_MIN_AUDIO_SEC", "VEXA_WORKSPACE_MOUNT_TARGET", "PATH", "HOME", "PYTHONPATH",
                "PLAYWRIGHT_BROWSERS_PATH", "VEXA_HF_CACHE"):
        assert env.get(key) == inherited[key], key


def test_supervisord_starts_the_runtime_through_the_launcher():
    command, _env = _rendered_programs()["runtime"]
    assert command.endswith("/usr/local/bin/vexa-runtime")


def test_only_the_runtime_and_its_two_callers_hold_the_caller_credential():
    programs = _rendered_programs()
    assert HOLDERS <= set(programs)
    holders = {name for name, (_cmd, env) in programs.items() if "RUNTIME_API_TOKEN" in env}
    assert holders == HOLDERS
    assert all(env["RUNTIME_API_TOKEN"] == TOKEN for name, (_cmd, env) in programs.items() if name in holders)
    assert not any(TOKEN in cmd for cmd, _env in programs.values())


def test_the_caller_credential_is_never_exported_or_baked():
    assert "RUNTIME_API_TOKEN" not in _exports()
    assert "RUNTIME_API_TOKEN" not in _image_env()
    assert re.search(r"^unset RUNTIME_API_TOKEN$", ENTRYPOINT.read_text(), flags=re.M)


def test_supervisord_runs_the_rendered_config():
    cmd = re.search(r"^CMD (.*)$", DOCKERFILE.read_text(), flags=re.M).group(1)
    assert "/run/vexa/supervisord.conf" in cmd
    assert "/etc/supervisor/conf.d/vexa.conf /run/vexa/supervisord.conf" in ENTRYPOINT.read_text()
    assert "@RUNTIME_API_TOKEN@" in SUPERVISORD.read_text()


def test_the_renderer_refuses_a_credential_that_could_break_the_config(tmp_path):
    render = _load(RENDERER, "render_supervisord").render
    for bad in ("", "short", 'a"' + "b" * 40, "a," + "b" * 40, "%(ENV_X)s" + "b" * 40):
        try:
            render(SUPERVISORD.read_text(), bad)
        except ValueError:
            continue
        raise AssertionError(f"rendered with {bad!r}")
    try:
        render("[program:x]\n", TOKEN)
    except ValueError:
        pass
    else:
        raise AssertionError("rendered a template that names no credential")


def test_the_renderer_writes_a_root_only_copy(tmp_path):
    import os
    import subprocess
    import sys

    out = tmp_path / "run" / "supervisord.conf"
    subprocess.run([sys.executable, str(RENDERER), str(SUPERVISORD), str(out)], input=TOKEN,
                   text=True, check=True)
    assert TOKEN in out.read_text() and "@RUNTIME_API_TOKEN@" not in out.read_text()
    assert out.stat().st_mode & 0o777 == 0o600
    assert (out.parent.stat().st_mode & 0o077) == 0
    bad = subprocess.run([sys.executable, str(RENDERER), str(SUPERVISORD), str(tmp_path / "x.conf")],
                         input="short", text=True, capture_output=True)
    assert bad.returncode == 1 and not (tmp_path / "x.conf").exists() and "short" not in bad.stderr
