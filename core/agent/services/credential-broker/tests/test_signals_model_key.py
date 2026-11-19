"""The signals role: the transcript-signals service reads the signal model's key the company policy
names — only `service/signals/<name>`, only with the signals role key, audited, never anyone's
connection — and the operator stores it from stdin."""
import io

from credential_broker import signals_key
from credential_broker.routes_signals import record_path

PATH = "/api/internal/signals/model-key"


def test_the_signals_role_reads_the_named_key(signed, store):
    store.put(record_path("openrouter"), {"owner": "signals", "value": "MODEL-SECRET"})
    r = signed("signals", "POST", PATH, {"name": "openrouter"}, actor="signals")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "openrouter", "value": "MODEL-SECRET"}


def test_a_missing_key_is_404(signed):
    assert signed("signals", "POST", PATH, {"name": "nothing"}, actor="signals").status_code == 404


def test_every_other_role_is_refused(signed, store):
    store.put(record_path("openrouter"), {"owner": "signals", "value": "MODEL-SECRET"})
    for role in ("agent", "human", "git", "bots", "sensors"):
        r = signed(role, "POST", PATH, {"name": "openrouter"}, actor="signals")
        assert r.status_code in (401, 403), role
        assert "MODEL-SECRET" not in r.text


def test_a_name_cannot_reach_outside_the_signals_namespace(signed):
    for name in ("../pat/u1", "pat/u1", "Openrouter", "", "a" * 70):
        assert signed("signals", "POST", PATH, {"name": name}, actor="signals").status_code == 422, name


def test_a_record_sealed_to_another_owner_is_not_served(signed, store):
    store.put(record_path("openrouter"), {"owner": "someone", "value": "MODEL-SECRET"})
    r = signed("signals", "POST", PATH, {"name": "openrouter"}, actor="signals")
    assert r.status_code == 503 and "MODEL-SECRET" not in r.text


def test_the_operator_command_stores_from_stdin(store, monkeypatch, capsys):
    monkeypatch.setattr(signals_key, "build_store", lambda settings: store)
    monkeypatch.setattr(signals_key, "load", lambda: None)
    assert signals_key.main(["put", "openrouter"], stdin=io.StringIO("MODEL-SECRET\n")) == 0
    assert store.get(record_path("openrouter")).data == {"value": "MODEL-SECRET", "owner": "signals"}
    assert "MODEL-SECRET" not in capsys.readouterr().out
    assert signals_key.main(["put", "openrouter"], stdin=io.StringIO("")) == 2
    assert signals_key.main(["put", "Bad/Name"], stdin=io.StringIO("x")) == 2
