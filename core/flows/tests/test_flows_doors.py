"""The doors flows reaches, and why none of them has a host-port default.

Found live on 2026-09-03: a bare `pytest` run of `test_admin_user_lookup_shapes` read a 403 from
an admin-api that was not this stack's. `VEXA_FLOWS_ADMIN_API_URL` defaulted to
`http://localhost:18057`, and on that host 18057 belongs to a DIFFERENT deployment. The test was
green-and-wrong for the worst possible reason: the default worked, against a neighbour.

So a door is `required-explicit` — no default at all — and the only defaults allowed anywhere name
a SERVICE (`http://admin-api:8057`), which resolves inside one deployment's network and nowhere
else. The agent door is the single exception, and a different kind of thing: `capability`, where
unset means the agent domain is not deployed (PRD decision 40.7).
"""
from __future__ import annotations

import re
from pathlib import Path

import flows_config
import pytest

#: Every door, and what its declaration class must be.
DOORS = {
    "VEXA_FLOWS_GATEWAY_URL": "required-explicit",
    "VEXA_FLOWS_ADMIN_API_URL": "required-explicit",
    "VEXA_UI_URL": "required-explicit",
    "VEXA_FLOWS_AGENT_API_URL": "capability",       # 40.7: unset = the domain is not deployed
}

_HOST_PORT = re.compile(r"//(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?", re.I)


@pytest.mark.parametrize("key,cls", sorted(DOORS.items()))
def test_a_door_has_no_default(key, cls):
    declared_cls, default, _why = flows_config.DECLARED[key]
    assert declared_cls == cls
    assert default is None, f"{key} has the default {default!r} — a door may not have one"


def test_no_declared_default_anywhere_names_a_local_host_port():
    """Not only the doors. Any host-port default in this table addresses whatever else is listening
    on the machine, which is the failure this row is about."""
    offenders = {k: d for k, (_c, d, _w) in flows_config.DECLARED.items()
                 if isinstance(d, str) and _HOST_PORT.search(d)}
    # `VEXA_MAILPIT_URL` is the declared exception and states why: mailpit is a DEVELOPMENT inbox
    # that only ever runs beside the process reading it, and it is a capability — unset means "not
    # using mailpit", so the default is never reached by a deployment.
    offenders.pop("VEXA_MAILPIT_URL", None)
    assert offenders == {}, f"host-port defaults: {offenders}"


def test_the_preflight_names_every_door_the_deployment_did_not(monkeypatch):
    for key, cls in DOORS.items():
        if cls == "required-explicit":
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(flows_config.ConfigError) as exc:
        flows_config.preflight()
    said = str(exc.value)
    for key, cls in DOORS.items():
        if cls == "required-explicit":
            assert key in said, f"the refusal does not name {key}"
    assert "VEXA_FLOWS_AGENT_API_URL" not in said, \
        "the agent door is a capability — its absence is a product, not a misconfiguration"


def test_the_preflight_passes_once_the_doors_are_named(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_GATEWAY_URL", "http://gateway:8000")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8001")
    monkeypatch.setenv("VEXA_UI_URL", "http://terminal:3000")
    monkeypatch.delenv("VEXA_FLOWS_AGENT_API_URL", raising=False)
    flows_config.preflight()                     # the no-agents profile boots


def test_require_refuses_rather_than_returning_an_empty_base(monkeypatch):
    """An empty base does not fail — it silently produces a RELATIVE url, which is the same class
    of bug one level down."""
    monkeypatch.delenv("VEXA_FLOWS_ADMIN_API_URL", raising=False)
    with pytest.raises(flows_config.ConfigError):
        flows_config.require("VEXA_FLOWS_ADMIN_API_URL")


def test_the_door_constants_resolve_at_access_not_at_import(monkeypatch):
    """A module constant binds whatever the environment said when the module was first imported —
    which is how a test process that never declared a door still had one."""
    from flows_steps import common
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api.example:8001")
    assert common.ADMIN_API == "http://admin-api.example:8001"
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://somewhere-else:8001")
    assert common.ADMIN_API == "http://somewhere-else:8001"


# ── the smoke reads its target from the contract, and skips when there is none ───────────────────

def test_the_contract_smoke_takes_its_target_from_the_contract_only():
    """The smoke must not carry its own idea of where a service lives — that idea is what pointed
    it at a neighbouring stack. It reads the door, and when the door is unnamed it SKIPS."""
    src = (Path(__file__).resolve().parent / "test_contract_smokes.py").read_text()
    assert not _HOST_PORT.search(src), "the contract smoke hard-codes a local host-port"
    assert "flows_config" in src or "from flows_steps.common import" in src


# ── PRD decision 18(d) · the database is a door too, and no source reaches into a container ─────

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_product_source_shells_out_to_a_named_container():
    """`common.db_url` read the Postgres password with
    `docker exec vexa-v012-postgres-1 sh -c 'echo -n $POSTGRES_PASSWORD'`, and
    `meeting.run_meeting` injected fixture transcript rows through `docker exec … psql` into the
    MEETINGS database. Both made the flows service depend on a named container of one developer's
    other stack, on one host — a dependency no deployment can satisfy, no contract can declare,
    and no operator can see until it fails. And the second one wrote into another domain's
    database directly, past its API."""
    offenders = {}
    for f in sorted(SRC.rglob("*.py")):
        bad = [f"{n}: {line.strip()}" for n, line in enumerate(f.read_text().splitlines(), 1)
               if "vexa-v012" in line or "subprocess" in line]
        if bad:
            offenders[f.relative_to(SRC).as_posix()] = bad
    assert offenders == {}, offenders


def test_the_database_url_is_a_required_door_with_no_fallback():
    cls, default, _why = flows_config.DECLARED["VEXA_FLOWS_DB_URL"]
    assert cls == "required-explicit"
    assert default is None


def test_an_unnamed_database_is_refused_rather_than_guessed(monkeypatch):
    """A guessed DSN on a host that runs more than one stack is the `localhost:18057` bug with a
    password on the end: it does not fail, it addresses somebody else's data."""
    from flows_steps import common
    monkeypatch.delenv("VEXA_FLOWS_DB_URL", raising=False)
    with pytest.raises(flows_config.ConfigError) as e:
        common.db_url()
    assert "VEXA_FLOWS_DB_URL" in str(e.value)


def test_a_named_database_is_returned_unchanged(monkeypatch):
    """The half a blanket refusal would break — including the offline dialect the whole suite and
    `gate:health` run on."""
    from flows_steps import common
    monkeypatch.setenv("VEXA_FLOWS_DB_URL", "sqlite://")
    assert common.db_url() == "sqlite://"
