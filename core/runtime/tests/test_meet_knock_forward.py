"""The Google Meet knock settings reach every meeting bot from the runtime environment (2 Nov 2026).

Meet refuses a share of anonymous knocks at random, so how many knocks a bot takes, the extra wait
between them and the guest-state wipe are deployment settings: set on the runtime (chart
`vexa.runtime.extraEnv`), forwarded into each new meeting-bot Pod, read by the bot at join time. A
change needs no new image; it applies to bots spawned after the runtime restarts with it.
"""
from runtime_kernel.profiles import default_registry

KEYS = ("VEXA_GOOGLE_JOIN_MAX_KNOCKS", "VEXA_GOOGLE_REKNOCK_BACKOFF_MS", "VEXA_GOOGLE_REKNOCK_WIPE")


def _clear(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_set_values_are_forwarded_to_meeting_bots(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_GOOGLE_JOIN_MAX_KNOCKS", "10")
    monkeypatch.setenv("VEXA_GOOGLE_REKNOCK_BACKOFF_MS", "5000,10000")
    monkeypatch.setenv("VEXA_GOOGLE_REKNOCK_WIPE", "0")
    env = default_registry().get("meeting-bot").base_env
    assert {k: env[k] for k in KEYS} == {
        "VEXA_GOOGLE_JOIN_MAX_KNOCKS": "10",
        "VEXA_GOOGLE_REKNOCK_BACKOFF_MS": "5000,10000",
        "VEXA_GOOGLE_REKNOCK_WIPE": "0",
    }


def test_unset_values_are_not_forwarded(monkeypatch):
    _clear(monkeypatch)
    env = default_registry().get("meeting-bot").base_env
    assert not set(KEYS) & set(env)


def test_agent_workers_do_not_get_them(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_GOOGLE_JOIN_MAX_KNOCKS", "10")
    reg = default_registry()
    for name in reg.names() if hasattr(reg, "names") else []:
        if name != "meeting-bot":
            assert "VEXA_GOOGLE_JOIN_MAX_KNOCKS" not in (reg.get(name).base_env or {})
