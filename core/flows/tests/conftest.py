import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


#: THE FLOWS KEY, declared the way a deployment declares it — AT IMPORT, beside the doors.
#:
#: Every call flows makes into another domain goes through the gateway's service door with this key
#: (`common.door_headers`), and an unset value REFUSES rather than trying a placeholder. That makes
#: every offline test that touches a step raise on the refusal instead of on the thing it is
#: testing, so the suite declares a key, exactly as a deployment does.
#:
#: NOT AN AUTOUSE FIXTURE: a fixture runs far too late (F-D20 b) — flows-api validates its whole
#: `config.v1` declaration at import, and several test modules import it at MODULE SCOPE, during
#: collection, before any fixture has run.
#:
#: `setdefault` semantics on purpose: a run that already exports a real key keeps it. The tests
#: that are ABOUT the refusal unset it themselves with `monkeypatch.delenv`, which is per-test.
os.environ.setdefault("VEXA_FLOWS_SERVICE_KEY", "test-flows-service-key-not-a-placeholder")


#: The doors, declared for the test process the way a deployment declares them. NOT autouse-set to
#: localhost: the whole point of the change that made these required is that a host-port default
#: silently addresses whatever else is listening — on 2026-09-03 a bare `pytest` run of
#: `test_admin_user_lookup_shapes` reached `vexa-v012`'s admin-api through the old
#: `http://localhost:18057` default and read its 403 as this stack's answer. A test that wants a
#: LIVE service exports the real URL (the contract smoke does, and skips when it is absent); every
#: offline test gets an address that is unmistakably not a service, so a step that tries to reach
#: one fails loudly instead of hitting a neighbour.
OFFLINE_DOORS = {
    # `127.0.0.1:1` on purpose. Port 1 is never a service, so a step that reaches a door it should
    # not have gets an immediate CONNECTION REFUSED — the same shape the old localhost defaults
    # produced when nothing was listening, and never the shape they produced when something was.
    # A `.invalid` hostname would be tidier to read and worse to run: it costs a DNS lookup that
    # fails as a different error class, so an offline test would diverge from a real refusal.
    "VEXA_FLOWS_GATEWAY_URL": "http://127.0.0.1:1",
    "VEXA_FLOWS_ADMIN_API_URL": "http://127.0.0.1:1",
    "VEXA_UI_URL": "http://ui.test",
    # The agent door is a CAPABILITY (PRD decision 40.7) and its absence is a supported product —
    # but almost every test in this suite is about the FULL profile, so the suite declares it and
    # `test_no_agents.py` unsets it deliberately for the one contract that is about the other one.
    "VEXA_FLOWS_AGENT_API_URL": "http://127.0.0.1:1",
}


# APPLIED AT IMPORT, not in a fixture. `flows_steps/meeting.py` and friends resolve their door at
# module import, so a fixture would run long after collection has already failed — and making the
# doors lazy enough to be set per-test would put the import-time refusal back where it cannot be
# seen. A test process declares its doors before anything imports a step module, exactly as a
# deployment does before it boots.
for _key, _value in OFFLINE_DOORS.items():
    os.environ.setdefault(_key, _value)


#: `sys.path` also carries the TEST directory, so `from sqlite_double import SqliteDB` resolves the
#: same way `fixtures.py` already resolves it when a test is collected from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("VEXA_FLOWS_API_KEY", "test-operator-key")


# ── the production registry, composed per test ───────────────────────────────────────────────────
# `fixtures.rig()` builds the FAKE world (`flows_steps.fakes`) against `flows_defs.defs` — the right
# rig for the failure matrix and the storm, and the wrong one for a test that is about what
# `flows_defs.production.build` composes in a deployment. These three fixtures are that second rig:
# the real definitions, the real steps, no world. They are separate fixtures rather than a second
# `rig()` so nothing above changes shape.
@pytest.fixture
def db():
    from sqlite_double import SqliteDB
    return SqliteDB()


@pytest.fixture
def clock():
    from flows import FakeClock
    return FakeClock(1_000_000.0)


@pytest.fixture
def registry(db, monkeypatch):
    """NO DOOR IS NAMED UNLESS A TEST NAMES IT.

    `domain_present` reads the module attributes `AGENT_API` / `MEETINGS_API`, which are resolved
    from the environment — so a developer with a stack's env sourced would compose a different
    registry than CI does, and the agent half would register or not depending on their shell. The
    same applies to `VEXA_FLOWS_DEFS_EXTRA`: a pack in someone's environment must not join the
    registry a test is asserting about.

    Scoped to this fixture on purpose. As an autouse it would also blank the doors `OFFLINE_DOORS`
    declares for the whole suite above, and `test_no_agents.py` is precisely about owning that
    unset itself.
    """
    from flows import Registry
    from flows_defs import production
    from flows_steps import common
    monkeypatch.setattr(common, "AGENT_API", "", raising=False)
    monkeypatch.setattr(common, "MEETINGS_API", "http://meetings.invalid", raising=False)
    monkeypatch.delenv("VEXA_FLOWS_DEFS_EXTRA", raising=False)
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR", raising=False)
    reg = Registry()
    production.build(reg, db)
    return reg


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# ── THE MAIL-KIND SWITCHES, FOR THE SUITES THAT PREDATE THEM ─────────────────────────────────────
# `mail.<kind>` in `_global/POLICIES.md` (flows_steps/mailkinds.py) ships most kinds OFF until the
# global account turns them on. Every suite written before the switches existed is a test of what a
# kind SAYS once it is on, so they run with every kind on — overlaid on whatever `policies.read`
# answers, so a suite that writes its own POLICIES still decides what it writes.
# A test about the switches themselves opts out with `@pytest.mark.mail_defaults`.
@pytest.fixture(autouse=True)
def _mail_kinds_on(request, monkeypatch):
    if request.node.get_closest_marker("mail_defaults"):
        yield
        return
    # The operator's own answer for "every kind on unless _global says otherwise" — read per call,
    # so it holds for every copy of the modules, including suites that purge and re-import them.
    monkeypatch.setenv("VEXA_MAIL_KINDS_DEFAULT", "on")
    yield


# ── THE OFFLINE AGENT DOOR HOLDS NO POLICY FILE ──────────────────────────────────────────────────
# `policies.read` tells a file that is not there (the defaults) from one that could not be read (the
# CLOSED rules, S03-1). The offline agent door refuses every connection, which is "could not be
# read" — so every suite that never meant to say anything about the policy file would run with the
# rules closed. Here the offline door answers "no POLICIES.md" (404); a suite that writes its own
# POLICIES patches `policies.ws_file` (or `policies.read`) as before, and a test about the
# unreachable door itself opts out with `@pytest.mark.policy_door`.
@pytest.fixture(autouse=True)
def _offline_door_has_no_policy_file(request, monkeypatch):
    if request.node.get_closest_marker("policy_door"):
        yield
        return
    import importlib
    import types
    importlib.import_module("flows_steps.policies")
    # EVERY COPY: a sibling suite purges `flows_steps.*` from sys.modules, so a step module (or a
    # test module) can still hold the policies module it imported before the purge.
    copies = {id(m): m for m in [sys.modules.get("flows_steps.policies"), *(
        v for mod in list(sys.modules.values()) if isinstance(mod, types.ModuleType)
        for v in (getattr(mod, "policies", None),))]
        if isinstance(m, types.ModuleType) and m.__name__ == "flows_steps.policies"}
    offline = OFFLINE_DOORS["VEXA_FLOWS_AGENT_API_URL"]
    for copy in copies.values():
        real = copy.ws_get

        def ws_get(uid, path, slug=None, _real=real):
            # the door as the copy's OWN `common` holds it (a test may blank or move it)
            if (getattr(_real, "__globals__", {}).get("AGENT_API") or "").rstrip("/") == offline:
                return 404, {"detail": "not found"}
            return _real(uid, path, slug)

        monkeypatch.setattr(copy, "ws_get", ws_get)
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "mail_defaults: run with the shipped mail-kind switches")
    config.addinivalue_line("markers", "policy_door: the offline agent door is unreachable for POLICIES.md too")
