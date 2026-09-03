"""flows-api as a COMPOSE SERVICE — the three things that stopped it being one.

Today the flows lanes run on the host out of `~/.storm/flows-up.sh`, on loopback :18200/:18201, and
the compose-network `mcp` service reaches them through an interim hack: the lane is bound to the
docker bridge address so the assembler can fetch `/.well-known/mcp-tools.json`. That interim is a
host-specific address written into a deployment, and it is what a real service replaces.

Three gaps, and each is small enough to look like nothing:

  * `main()` binds `127.0.0.1`. Inside a container that is the loopback of the container, so the
    service is unreachable from every other service on the network — the single reason the bridge
    address had to be hand-wired.
  * there is no `/health`, so nothing can express a compose `healthcheck` or a `depends_on`
    condition against it.
  * the manifest route exists but nothing pins WHICH tools it serves, so an assembly that silently
    lost a tool would still look assembled.

Offline: the engine is stdlib-pure and `postgres_db` builds its engine lazily, so the app imports
without a database.
"""
from __future__ import annotations

import os

import pytest

# IMPORTING THIS APP HAS SIDE EFFECTS, so every one of them is undone before the module finishes.
#
# `flows_api` reads its credentials and composes its database AT IMPORT. Getting it in here means
# setting environment, PROCESS-WIDE. Left in place it leaks into every other test file in the
# session, which is not hypothetical: the first version of this module turned
# `test_link_loop.py::test_mint_scaffold_posts_the_record_and_returns_the_url` red, and that test
# passes on its own. `gate:test-isolation` exists for this shape.
#
# THE DATABASE IS CHOSEN BY THE URL, not by a monkeypatch. An earlier version of this file swapped
# `flows.postgres_db` for an in-memory sqlite adapter; `flows_api` then moved to `db_from_url`,
# which resolves `postgres_db` inside `flows.db` and never saw the swap, so the module tried to
# open a real connection at import. `db_from_url`'s own docstring is the fix: "a `sqlite://` URL
# gets the sqlite dialect, anything else gets Postgres. No flag, no test-only branch — the
# deployment's own URL decides." So this suite is a deployment whose URL says sqlite.
_PRIOR_ENV = {k: os.environ.get(k) for k in
              ("VEXA_FLOWS_API_KEY", "INTERNAL_API_SECRET", "VEXA_FLOWS_DB_URL")}

os.environ.setdefault("VEXA_FLOWS_API_KEY", "test-flows-key")
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ["VEXA_FLOWS_DB_URL"] = "sqlite://"
try:
    from flows_integrations import flows_api  # noqa: E402
finally:
    for _k, _v in _PRIOR_ENV.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

#: The domain's contribution to the one MCP surface. Named here so a tool cannot leave the manifest
#: without a test saying so — an assembly that quietly lost one still looks assembled.
FOUR_TOOLS = {"flows_list", "reactions_list", "reaction_signal", "timeline"}


def _route(path: str):
    return next((r for r in flows_api.app.routes if getattr(r, "path", None) == path), None)


def test_health_exists_and_is_open():
    """A compose healthcheck and a `depends_on: service_healthy` both need one unauthenticated
    route that answers. Open like the manifest: a probe that had to authenticate could not run
    before identity did, which is the moment you most want to know whether the process is up."""
    r = _route("/health")
    assert r is not None, "flows-api has no /health — nothing can express a healthcheck against it"
    assert not getattr(r, "dependencies", []), "/health must not be behind auth"

    body = flows_api.health()
    assert body.get("status") == "ok", body
    assert body.get("service") == "flows-api", body


def test_no_path_is_registered_twice():
    """This branch and #1431 both added a /health, and the collision is SILENT: FastAPI keeps both
    registrations and the FIRST one answers, so the reader of the second body is reading dead code
    that looks live. `_route` returns the first match, which is what the framework does too — this
    is the assertion that makes a second one a failure instead of a shadow."""
    bindings = [(m, r.path) for r in flows_api.app.routes if getattr(r, "path", None)
                for m in sorted(getattr(r, "methods", None) or ["*"])]
    duplicates = sorted({b for b in bindings if bindings.count(b) > 1})
    assert duplicates == [], duplicates


def test_nothing_is_registered_after_the_entrypoint_guard():
    """FOUND BY THE FIRST LIVE BOOT, and invisible to every offline assertion above.

    `if __name__ == "__main__": raise SystemExit(main())` sat in the MIDDLE of the module, above
    the manifest route. Every deployment starts this file with `python -m`, so `__name__` IS
    `__main__`: the guard fires, `main()` blocks inside `uvicorn.run`, and the lines below it never
    execute. The running service answered 404 on `/.well-known/mcp-tools.json` — the one route the
    MCP assembly needs — while this suite, which IMPORTS the module and therefore runs all of it,
    proved the route existed and served exactly the right four tools.

    A test that imports can never catch this, so this one reads the source instead."""
    from pathlib import Path

    src = Path(flows_api.__file__).read_text()
    guard = src.index('if __name__ == "__main__":')
    after = src[guard:]
    assert "@app." not in after, \
        "a route is declared after the entrypoint guard — `python -m` never reaches it"
    assert after.count("\n") < 4, "the guard must be the LAST statement in the module"


def test_the_manifest_is_open_and_carries_exactly_the_four_tools():
    r = _route("/.well-known/mcp-tools.json")
    assert r is not None
    assert not getattr(r, "dependencies", []), "the manifest must stay open — the assembler is not a user"

    manifest = flows_api.mcp_tools_manifest()
    assert {t["name"] for t in manifest["tools"]} == FOUR_TOOLS, manifest["tools"]
    assert manifest["domain"] == "flows"


def test_the_bind_address_is_configurable_and_still_defaults_to_loopback():
    """The container binds every interface; the HOST LANE must not change under the dogfood estate.

    A default of 0.0.0.0 would silently expose the host lane's port on every interface of the rig
    box the day this merges — a deployment change smuggled in as a container fix. So the default
    stays loopback and the container says otherwise, out loud, in its own environment.
    """
    assert flows_api.bind_host() == "127.0.0.1"

    os.environ["VEXA_FLOWS_API_HOST"] = "0.0.0.0"
    try:
        assert flows_api.bind_host() == "0.0.0.0"
    finally:
        del os.environ["VEXA_FLOWS_API_HOST"]


def test_the_new_keys_are_declared():
    """`flows_config.DECLARED` asserts both directions (read ⊆ declared and declared ⊆ read), so a
    key added without a declaration fails there. This says it out loud where the key is introduced."""
    import flows_config

    for key in ("VEXA_FLOWS_API_HOST", "VEXA_FLOWS_API_PORT"):
        assert key in flows_config.DECLARED, f"{key} is read and declared nowhere"


def test_a_step_time_door_is_not_read_at_import(monkeypatch):
    """A REQUIRED-EXPLICIT door that is only used to compose a mailed link must not decide whether
    the process can BOOT. Found by the live proof on bbb: flows-api crash-looped with
    `VEXA_UI_URL is unset` before serving anything, including /health and the tool manifest.

    `_door` resolves at ACCESS time on purpose — its own docstring says a constant "binds whatever
    the environment said at import" — and `common.__getattr__` (PEP 562) is what makes that work.
    But `from flows_steps.common import UI_URL` fires that `__getattr__` AT IMPORT, which is the
    one thing the design was built to avoid. One `from … import` undid it for the whole module.

    So: a deployment that never mails a link boots; the refusal still fires, loudly, at the moment
    the link would have been composed.
    """
    import importlib
    import sys

    monkeypatch.delenv("VEXA_UI_URL", raising=False)
    monkeypatch.setenv("VEXA_FLOWS_GATEWAY_URL", "http://gateway:8000")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8001")
    for mod in [m for m in sys.modules if m.startswith(("flows_defs", "flows_steps"))]:
        del sys.modules[mod]

    importlib.import_module("flows_defs.production")      # must not raise

    # AND THE OTHER HALF, which the first version of this test missed: `flows_api` calls
    # `flows_config.preflight()` at import, and VEXA_UI_URL was a required-explicit DOOR, so the
    # process still refused to boot with the import fixed. It is a link PORT now (PRD decision 4)
    # and its adapter is optional, so a deployment with no terminal boots.
    import flows_config
    flows_config.preflight()                              # must not raise

    from flows_steps import common
    with pytest.raises(Exception) as refused:
        _ = common.UI_URL
    assert "VEXA_UI_URL" in str(refused.value), "the refusal must still name the key it needs"
