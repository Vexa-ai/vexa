"""The instance gate: a Vexa with no company layer must not act on the world.

Three things are under test and they are deliberately separate concerns:

  1. `flows_integrations/instance_gate.py` — the single reader. Fail-closed on every error path,
     cached so `tick` cannot poll admin-api to death, logged on TRANSITION so an operator can tell
     "admin-api is down" from "the admin has not finished setup".
  2. `flows/loop.py` — PARK, never drop. A closed gate must leave every reaction byte-identical:
     same status, same step, same attempt, same next_run_at. A parked fact that retried itself
     into `failed` would be a dropped fact, just slower.
  3. `flows_integrations/flows_api.py` — intakes admit and SAY they parked; the two operator verbs
     refuse; reads stay open. Asserted against the source, because importing that module opens a
     Postgres connection and mints an API key at import time (the same reason no other test in
     this suite imports it).

No network, no admin-api, no clock, no Postgres — the HTTP read is replaced at the seam and the
engine half runs on the sqlite fixture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows.loop as loop_mod  # noqa: E402
import flows_integrations.instance_gate as gate  # noqa: E402
import flows_steps.common as common  # noqa: E402
from fixtures import INVITE_REFS, drain, rig  # noqa: E402
from flows import admit, tick  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    """Both pieces of module state are process-global by design (a cache that reset per call is
    not a cache; a transition log that reset per call is a flood). So every test starts from a
    forgotten gate, or the second assertion reads the first test's world."""
    monkeypatch.delenv("VEXA_FLOWS_INSTANCE_GATE", raising=False)
    gate.reset_cache()
    loop_mod._GATE_UP = False
    yield
    gate.reset_cache()
    loop_mod._GATE_UP = False


def _http(answer):
    """Replace the ONE http call the gate makes. `answer` is (code, body) or an exception to
    raise — `common.http` itself never leaks a raw urllib error, it raises StepError."""
    calls = []

    def fake(method, url, headers, body=None, timeout=20):
        calls.append({"method": method, "url": url, "headers": headers, "timeout": timeout})
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return fake, calls


# ── 1. fail-closed ───────────────────────────────────────────────────────────────────────────
def test_an_unreachable_admin_api_closes_the_gate(monkeypatch):
    """The branch the whole module exists for: silence is not permission. A flow that mails a
    stranger because admin-api happened to be restarting is the expensive failure."""
    fake, _ = _http(RuntimeError("connection refused"))
    monkeypatch.setattr(common, "http", fake)
    assert gate.gate_state() == "missing"
    assert gate.company_layer_ready() is False


@pytest.mark.parametrize("answer", [
    (200, {"admin_exists": True, "global_setup": "missing", "company": None}),   # honestly unset
    (200, {"admin_exists": False, "company": None}),                             # key absent
    (200, {"global_setup": "in_progress"}),                                      # not the token
    (200, {"global_setup": True}),                                               # wrong type
    (200, "Service Unavailable"),                                                # not a document
    (500, {"detail": "boom"}),
    (404, {"detail": "no such route"}),                                          # older admin-api
    (401, {"detail": "bad key"}),                                                # misconfigured
])
def test_everything_that_is_not_a_completed_document_reads_missing(monkeypatch, answer):
    fake, _ = _http(answer)
    monkeypatch.setattr(common, "http", fake)
    assert gate.gate_state() == "missing"


def test_only_a_completed_document_opens_the_gate(monkeypatch):
    fake, calls = _http((200, {"admin_exists": True, "global_setup": "completed",
                               "company": "Vexa"}))
    monkeypatch.setattr(common, "http", fake)
    assert gate.gate_state() == "completed"
    assert gate.company_layer_ready() is True
    # the contract: the admin door the flows tier already holds, read-only
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/admin/instance")
    assert calls[0]["headers"]["X-Admin-API-Key"] == common.ADMIN_KEY


# ── 2. the cache ─────────────────────────────────────────────────────────────────────────────
def test_the_answer_is_cached_so_tick_cannot_poll_admin_api_to_death(monkeypatch):
    fake, calls = _http((200, {"global_setup": "completed"}))
    monkeypatch.setattr(common, "http", fake)
    for _ in range(50):                       # what one worker does in under a minute
        assert gate.company_layer_ready() is True
    assert len(calls) == 1


def test_force_bypasses_the_cache(monkeypatch):
    fake, calls = _http((200, {"global_setup": "completed"}))
    monkeypatch.setattr(common, "http", fake)
    gate.gate_state()
    gate.gate_state(force=True)
    assert len(calls) == 2


def test_the_cache_expires(monkeypatch):
    fake, calls = _http((200, {"global_setup": "completed"}))
    monkeypatch.setattr(common, "http", fake)
    t = [1_000.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: t[0])
    gate.gate_state()
    t[0] += gate.TTL_S - 0.1
    gate.gate_state()
    assert len(calls) == 1                    # still inside the window
    t[0] += 1.0
    gate.gate_state()
    assert len(calls) == 2                    # and it does come back — never pinned forever


# ── 3. the test/dev seam ─────────────────────────────────────────────────────────────────────
def test_the_env_seam_short_circuits_the_http_read(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("the override must not touch the network at all")

    monkeypatch.setattr(common, "http", explode)
    monkeypatch.setenv("VEXA_FLOWS_INSTANCE_GATE", "completed")
    assert gate.gate_state() == "completed"
    gate.reset_cache()
    monkeypatch.setenv("VEXA_FLOWS_INSTANCE_GATE", "MISSING")     # case-insensitive
    assert gate.gate_state() == "missing"


def test_a_typo_in_the_override_does_not_open_the_door(monkeypatch):
    """`VEXA_FLOWS_INSTANCE_GATE=complete` is not `completed`. An unrecognised value falls
    through to the real read — which here cannot answer, so the gate stays closed."""
    fake, calls = _http(RuntimeError("connection refused"))
    monkeypatch.setattr(common, "http", fake)
    monkeypatch.setenv("VEXA_FLOWS_INSTANCE_GATE", "complete")
    assert gate.gate_state() == "missing"
    assert len(calls) == 1                    # it really did fall through, not short-circuit


# ── 4. the log records transitions, and names WHICH kind of missing ──────────────────────────
def test_the_reason_is_logged_once_per_transition_not_once_per_call(monkeypatch, capsys):
    fake, _ = _http((200, {"global_setup": "missing"}))
    monkeypatch.setattr(common, "http", fake)
    for _ in range(5):
        gate.gate_state(force=True)
    lines = [l for l in capsys.readouterr().out.splitlines() if "[instance-gate]" in l]
    assert len(lines) == 1


def test_a_gate_up_because_admin_api_is_down_reads_differently_from_unfinished_setup(
        monkeypatch, capsys):
    """Both are `missing`; one is an incident and one is Tuesday. If the log cannot tell them
    apart the operator has to guess, which is the whole reason the reason is carried."""
    down, _ = _http(RuntimeError("connection refused"))
    monkeypatch.setattr(common, "http", down)
    gate.gate_state(force=True)
    unfinished, _ = _http((200, {"global_setup": "missing", "admin_exists": True}))
    monkeypatch.setattr(common, "http", unfinished)
    gate.gate_state(force=True)
    lines = [l for l in capsys.readouterr().out.splitlines() if "[instance-gate]" in l]
    assert len(lines) == 2                                   # the state never changed; the reason did
    assert "unreachable" in lines[0]
    assert "not committed" in lines[1] and "unreachable" not in lines[1]


# ── 5. the loop PARKS — never drops ──────────────────────────────────────────────────────────
def _one_admitted_reaction():
    """One invite.received, admitted, nothing run yet — plus a world in which that meeting does
    eventually complete, so the ungated half of these tests drains instead of parking forever on
    await_completion (the storm seeds the same flag for the same reason)."""
    db, reg, clock, world = rig()
    admit(db, reg, clock, source_event_id="ev-1", event_type="invite.received",
          subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    return db, reg, clock, world


def _snapshot(db):
    return db.execute("SELECT reaction_id, step, status, attempt, next_run_at, lease_until, reason "
                      "FROM reaction ORDER BY reaction_id")


def test_a_closed_gate_claims_nothing_and_changes_nothing(capsys):
    db, reg, clock, world = _one_admitted_reaction()
    before = _snapshot(db)
    assert before and before[0][2] == "admitted"

    for _ in range(20):                       # twenty seconds of a real worker
        assert tick(db, reg, clock, gate=lambda: False) is False

    assert _snapshot(db) == before             # status, step, attempt, next_run_at, reason: all held
    assert db.execute("SELECT COUNT(*) FROM effect_receipt")[0][0] == 0
    assert world.meetings_created == [] and world.emails == []
    # …and the operator watching the log is told why nothing is moving, exactly once
    parked = [l for l in capsys.readouterr().out.splitlines() if "PARKED" in l]
    assert len(parked) == 1 and "1 reaction(s)" in parked[0]


def test_the_gate_is_asked_before_the_claim_so_no_lease_is_ever_taken():
    """Position matters: a gate checked AFTER claim would leave a leased, half-claimed row for
    the reconciler to mop up on every tick. Nothing may be leased while the gate is up."""
    db, reg, clock, world = _one_admitted_reaction()
    claims = []
    real_claim = loop_mod.claim
    loop_mod.claim = lambda *a, **k: claims.append(1) or real_claim(*a, **k)
    try:
        tick(db, reg, clock, gate=lambda: False)
    finally:
        loop_mod.claim = real_claim
    assert claims == []
    assert db.execute("SELECT COUNT(*) FROM reaction WHERE lease_until IS NOT NULL")[0][0] == 0


def test_a_parked_reaction_runs_normally_and_in_full_once_the_gate_opens(capsys):
    db, reg, clock, world = _one_admitted_reaction()
    for _ in range(20):
        tick(db, reg, clock, gate=lambda: False)

    assert tick(db, reg, clock, gate=lambda: True) is True            # the very next tick works
    drain(db, reg, clock)

    assert world.meetings_created == ["m-1"]                          # the fact was not lost
    assert db.execute("SELECT status FROM reaction")[0][0] == "done"
    assert "instance gate open — resuming" in capsys.readouterr().out


def test_the_gate_costs_nothing_when_it_is_absent():
    """`gate=None` is the default, so every existing caller — the fixtures, the storm, the whole
    offline suite — is unchanged. This is the regression guard on that promise."""
    db, reg, clock, world = _one_admitted_reaction()
    drain(db, reg, clock)
    assert world.meetings_created == ["m-1"]


def test_the_engine_core_still_imports_nothing_from_the_adapters():
    """`flows/` is the engine core and `flows_integrations/` the adapters. The gate is INJECTED
    by the worker precisely so this stays true; an import here would be a layering inversion."""
    src = (SRC / "flows" / "loop.py").read_text()
    assert "flows_integrations" not in src.replace(
        "`flows_integrations/`", "")                          # prose may name it; code may not
    worker = (SRC / "flows_worker" / "__main__.py").read_text()
    assert "gate=instance_gate.company_layer_ready" in worker


# ── 6. the API surface ───────────────────────────────────────────────────────────────────────
def _api_blocks() -> dict:
    """The flows-api source, split per endpoint. Read rather than imported: that module opens a
    Postgres connection and refuses to start without VEXA_FLOWS_API_KEY, both at import time."""
    src = (SRC / "flows_integrations" / "flows_api.py").read_text()
    blocks, name = {}, None
    for line in src.splitlines():
        if line.startswith("def ") or line.startswith("@app."):
            if line.startswith("def "):
                name = line[4:].split("(")[0]
                blocks[name] = []
        if name:
            blocks[name].append(line)
    return {k: "\n".join(v) for k, v in blocks.items()}


def test_the_two_operator_verbs_refuse_while_the_gate_is_up():
    b = _api_blocks()
    assert '_refuse_if_gated("flows_submit")' in b["submit_flow"]
    assert '_refuse_if_gated(f"flows_{action}")' in b["set_flow_status"]
    assert "status_code=409" in b["_refuse_if_gated"]


def test_the_intakes_still_admit_but_say_that_they_parked():
    """Admission is not refused — a fact that happened, happened. But a 202 that produces nothing
    for an hour, with no word said, is indistinguishable from a broken product."""
    b = _api_blocks()
    assert "_with_gate({" in b["admit_event"]
    assert "_with_gate({" in b["admit_batch"]
    assert '"gate": "missing"' in b["_with_gate"]
    assert "_refuse_if_gated" not in b["admit_event"]
    assert "_refuse_if_gated" not in b["admit_batch"]


def test_reading_stays_open_because_an_admin_must_see_the_machine():
    b = _api_blocks()
    for read_verb in ("list_flows", "list_reactions"):
        assert "_refuse_if_gated" not in b[read_verb]
        assert "_with_gate" not in b[read_verb]


def test_the_refusal_sentence_is_spelled_one_way_in_one_place():
    assert gate.SETUP_SENTENCE == "This Vexa is being set up by its administrator."
    src = (SRC / "flows_integrations" / "flows_api.py").read_text()
    assert gate.SETUP_SENTENCE not in src        # composed from the constant, never re-typed
    assert "instance_gate.SETUP_SENTENCE" in src
