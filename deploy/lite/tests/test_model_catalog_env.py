"""Lite takes the model catalog the way compose does (ADR-0043).

`VEXA_MODEL_CATALOG` is one JSON object on one line of `.env`, which `make up` hands the container
with `docker run --env-file` (a value runs to the end of its line, spaces and quotes included). The
entrypoint exports it as given, every program inherits it from supervisord, and agent-api — the one
reader — gets it unchanged. A provider's `secret_ref` names another `.env` variable that reaches
agent-api the same way. The runtime starts from a cleared environment, so neither the catalog nor a
catalog secret reaches a worker or a bot.

Offline: the rendered supervisor config, the entrypoint and the runtime launcher, read as text and
run as they ship.
"""
from __future__ import annotations

import json
import re
import subprocess

from test_runtime_environment import ENTRYPOINT, RUNTIME_SRC, _launcher, _rendered_programs

CATALOG = {"providers": {"lab-vllm": {"adapter": "openai_compatible", "base_url": "http://10.0.0.5:8000/v1",
                                      "auth": "none"},
                         "openrouter": {"adapter": "openrouter", "auth": "secret",
                                        "secret_ref": "env:OPENROUTER_API_KEY"}},
           "models": [{"id": "qwen3-32b", "display_name": "Qwen 3 32B (self-hosted)",
                       "provider": "lab-vllm", "model": "Qwen/Qwen3-32B", "default": True},
                      {"id": "or-sonnet", "display_name": "Claude Sonnet via OpenRouter",
                       "provider": "openrouter", "model": "anthropic/claude-sonnet-4.5"}]}


def _export_line() -> str:
    m = re.search(r'^export VEXA_MODEL_CATALOG=.*$', ENTRYPOINT.read_text(), flags=re.M)
    assert m, "the entrypoint does not export VEXA_MODEL_CATALOG"
    return m.group(0)


def test_the_entrypoint_passes_the_operators_catalog_through_byte_for_byte():
    """The export keeps a value the container was given — JSON with spaces, quotes and commas
    included — and defaults an absent one to empty (no catalog)."""
    value = json.dumps(CATALOG)
    script = f'{_export_line()}\nprintf %s "$VEXA_MODEL_CATALOG"'
    given = subprocess.run(["bash", "-c", script], env={"VEXA_MODEL_CATALOG": value, "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, check=True).stdout
    assert given == value
    absent = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin"},
                            capture_output=True, text=True, check=True).stdout
    assert absent == ""


def test_agent_api_inherits_the_catalog_and_does_not_override_it():
    _cmd, env = _rendered_programs()["agent-api"]
    assert env["VEXA_MODEL_CATALOG"] == "value-of-VEXA_MODEL_CATALOG"   # the export, untouched


def test_neither_the_catalog_nor_a_catalog_secret_reaches_the_runtime():
    _cmd, inherited = _rendered_programs()["runtime"]
    inherited = {**inherited, "OPENROUTER_API_KEY": "a-catalog-secret", "PATH": "/usr/bin",
                 "HOME": "/root", "PYTHONPATH": str(RUNTIME_SRC)}
    env = _launcher().runtime_environment(inherited, str(RUNTIME_SRC))
    assert "VEXA_MODEL_CATALOG" not in env
    assert "a-catalog-secret" not in env.values()
