"""IMPORTING THIS SERVICE IS NOT BOOTING IT — Vexa-ai/vexa#1629.

The candidate tag `v0.13.0-alpha.1` built all eleven images and then failed `validate /
image-identity` on one of them, `v012-flows`, because the identity probe IMPORTS the three
entrypoints the image carries and `flows_integrations.flows_api` refused at module scope:

    config_preflight.ConfigError: flows-api is misconfigured and refuses to boot — required
    environment variable(s) not set: VEXA_FLOWS_DB_URL (…)

`a90e442a3` had routed `_require_api_key` through the shared `config.v1` validator — the right
refusal — and module scope called it, which turned "this deployment is misconfigured" into "this
module cannot be read". Those are different claims with different audiences: the probe, `make
flow-pages` and every tool that imports the module to look at it are not deployments and have no
DSN. `main`'s v0.12.27-rc.3 passed only because its probe stopped at `importlib.util.find_spec`,
which resolves a module without executing it; the branch's probe imports for real (`6390c6f49`).

So the two halves are asserted here, and they are the whole contract:

  A  the release's identity probe imports `flows_worker`, `flows_integrations.mailbox` and
     `flows_integrations.flows_api` in a process with NO environment at all, and nothing raises;
  B  the STARTUP path — `boot()`, and the ASGI lifespan that runs it — still refuses without the
     DSN, with the same `ConfigError` naming the same key.

Every case runs in a SUBPROCESS. In-process these modules are already imported by six other test
modules (whichever imports first wins), and clearing `os.environ` for the session is exactly what
`gate:test-isolation` exists to stop — but a subprocess with a hand-built environment is also the
literal shape of `docker run --rm <image> python -c …`, so the test and the probe ask one question.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"

#: THE IMPORT HALF OF THE RELEASE'S IDENTITY PROBE, verbatim — one image, three entrypoints
#: (worker · mailbox · api) selected by command, so the identity claim is that all three import.
#: The probe's other half (`assert os.path.exists('/app/mcp.tools.v1.json')`) is about the IMAGE's
#: layout, which only the built image can answer; the source of both is
#: `.github/workflows/release-validate.yml`, job `image-identity`, case `vexaai/v012-flows`.
IDENTITY_PROBE = "import flows_worker, flows_integrations.mailbox, flows_integrations.flows_api"


def _bare_env() -> dict:
    """No `VEXA_*`, no `INTERNAL_*`, nothing this service reads — what `docker run --rm` gives it.

    `PATH` and `HOME` stay because an interpreter needs them; `PYTHONDONTWRITEBYTECODE` is set so a
    run of this suite never leaves a `src/__pycache__` behind for somebody to commit by accident.
    """
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if k in os.environ}
    keep["PYTHONPATH"] = str(_SRC)
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    return keep


def _configured_env() -> dict:
    """A complete, honest environment for this service, built from the declaration rather than from
    a list typed here — so a key added to `config.v1.json` is covered without editing this file.

    The DSN names an address nothing listens on: `postgres_db` opens no connection until the first
    query, so a boot is expected to SUCCEED against it. That is the point — the refusals under test
    are about naming the deployment, never about reaching it.
    """
    declaration = json.loads((_SRC / "config.v1.json").read_text(encoding="utf-8"))
    env = _bare_env()
    for entry in declaration.get("keys") or []:
        if entry.get("class") == "required-explicit":
            env[entry["key"]] = f"real-{entry['key'].lower().replace('_', '-')}"
    env["VEXA_FLOWS_DB_URL"] = "postgresql+psycopg://flows:pw@127.0.0.1:1/flows"
    # The doors `flows_config.preflight()` refuses without — flows' own rule, not the declaration's.
    env["VEXA_FLOWS_GATEWAY_URL"] = "http://127.0.0.1:1"
    env["VEXA_FLOWS_ADMIN_API_URL"] = "http://127.0.0.1:1"
    env["VEXA_UI_URL"] = "http://ui.test"
    env["VEXA_FLOWS_AGENT_API_URL"] = "http://127.0.0.1:1"
    return env


def _python(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True, timeout=180)


# ── A · the import refuses nothing ──────────────────────────────────────────────────────────────

def test_the_release_identity_probe_imports_with_no_environment_at_all():
    """The failing case from #1629, as the release runs it."""
    done = _python(IDENTITY_PROBE, _bare_env())
    assert done.returncode == 0, (
        "the identity probe cannot import this image's three entrypoints:\n" + done.stderr)


@pytest.mark.parametrize("module", ["flows_worker",
                                    "flows_integrations.mailbox",
                                    "flows_integrations.flows_api"])
def test_each_entrypoint_module_imports_on_its_own(module):
    """One at a time, so a failure names WHICH entrypoint stopped being readable."""
    done = _python(f"import {module}", _bare_env())
    assert done.returncode == 0, done.stderr


def test_an_unconfigured_import_authenticates_nobody():
    """Fail-closed, not merely quiet. Import stops refusing; it must not start ACCEPTING — an
    empty operator key is refused by `_same_key`, so a module that was imported and never booted
    opens nothing."""
    done = _python(
        "from flows_integrations import flows_api\n"
        "assert flows_api.API_KEY == '' and flows_api.TIMELINE_KEY == ''\n"
        "assert flows_api.INTERNAL_SECRET == ''\n"
        "assert not flows_api._same_key('', flows_api.API_KEY)\n"
        "assert not flows_api._same_key('changeme', flows_api.API_KEY)\n",
        _bare_env())
    assert done.returncode == 0, done.stderr


def test_a_placeholder_in_the_environment_is_read_as_no_key_at_all():
    """The literals in the declaration's `forbidden_values` are published in this repository, so
    holding one between import and boot would be worse than holding nothing."""
    env = _bare_env() | {"VEXA_FLOWS_API_KEY": "vexa-internal-secret"}
    done = _python(
        "from flows_integrations import flows_api\n"
        "assert flows_api.API_KEY == '', flows_api.API_KEY\n",
        env)
    assert done.returncode == 0, done.stderr


# ── B · the startup path still refuses ──────────────────────────────────────────────────────────

def test_boot_refuses_without_the_dsn():
    """Everything the declaration asks for except `VEXA_FLOWS_DB_URL` — the exact key #1629's
    traceback named."""
    env = _configured_env()
    env.pop("VEXA_FLOWS_DB_URL")
    done = _python("from flows_integrations import flows_api; flows_api.boot()", env)
    assert done.returncode != 0, "boot accepted a deployment with no database"
    assert "VEXA_FLOWS_DB_URL" in done.stderr
    assert "ConfigError" in done.stderr


def test_boot_refuses_a_placeholder_operator_key():
    """The other half of the refusal `a90e442a3` bought, kept: a key on a published literal does
    not boot, it just no longer stops the import."""
    env = _configured_env() | {"VEXA_FLOWS_API_KEY": "vexa-internal-secret"}
    done = _python("from flows_integrations import flows_api; flows_api.boot()", env)
    assert done.returncode != 0
    assert "VEXA_FLOWS_API_KEY" in done.stderr


def test_boot_refuses_a_deployment_with_no_internal_tier_identity():
    """`require_internal_secret()` was a module-scope call too, and it moved with the rest — so the
    key it guards is checked here rather than at the first post-meeting run."""
    env = _configured_env()
    env.pop("INTERNAL_API_SECRET")
    done = _python("from flows_integrations import flows_api; flows_api.boot()", env)
    assert done.returncode != 0
    assert "INTERNAL_API_SECRET" in done.stderr


def test_boot_refuses_a_deployment_that_cannot_name_its_admin_api_door():
    """The door question is asked at boot as well — `flows_config.preflight()` is inside `boot()`,
    which `tests/test_config_preflight_boot.py` asserts structurally. This is the behaviour: with
    everything else named, a process that cannot say where admin-api is does not start."""
    env = _configured_env()
    env.pop("VEXA_FLOWS_ADMIN_API_URL")
    done = _python("from flows_integrations import flows_api; flows_api.boot()", env)
    assert done.returncode != 0
    assert "VEXA_FLOWS_ADMIN_API_URL" in done.stderr


def test_a_configured_deployment_boots():
    """The control. Without it every refusal above could be passing for the wrong reason — and it
    asserts the thing boot is FOR: the module holds the validated key and a composed database."""
    done = _python(
        "from flows_integrations import flows_api\n"
        "out = flows_api.boot()\n"
        "assert out['service'] == 'flows-api' and out['steps'] > 0\n"
        "assert flows_api.API_KEY == 'real-vexa-flows-api-key', flows_api.API_KEY\n"
        "assert flows_api.db is not None\n",
        _configured_env())
    assert done.returncode == 0, done.stderr


# ── B · …and serving is booting, whoever runs the server ────────────────────────────────────────

_SERVE = ("from fastapi.testclient import TestClient\n"
          "from flows_integrations import flows_api\n"
          "with TestClient(flows_api.app) as client:\n"
          "    assert client.get('/health').status_code == 200\n"
          "print('SERVED')\n")


def test_the_asgi_lifespan_boots_the_app():
    """`main()` is not the only way this app is served — `uvicorn flows_integrations.flows_api:app`
    reaches it without running `main()` at all — so the refusal hangs off the lifespan, which every
    ASGI server runs before the first request."""
    done = _python(_SERVE, _configured_env())
    assert done.returncode == 0, done.stderr
    assert "SERVED" in done.stdout


def test_the_asgi_lifespan_refuses_to_serve_without_the_dsn():
    """The whole point of not refusing at import: an unconfigured deployment must still fail to
    SERVE, at startup, before a single request is answered."""
    env = _configured_env()
    env.pop("VEXA_FLOWS_DB_URL")
    done = _python(_SERVE, env)
    assert done.returncode != 0, "the app served without a database"
    assert "SERVED" not in done.stdout
    assert "VEXA_FLOWS_DB_URL" in done.stderr
