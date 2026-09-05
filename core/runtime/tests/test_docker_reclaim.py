"""``DockerBackend.cleanup`` against a SCRIPTED daemon — the reclaim contract, no docker needed.

The property under test: ``destroy`` may report ``destroyed`` only when the container is provably
gone. A 409 "removal already in progress" means another remover (an operator's ``rm -f``, a GC,
another deployment sharing the daemon) is finishing the reclaim — the reclaim is then judged by
the container's ABSENCE, confirmed within a bounded window, never by whose DELETE won. Everything
else keeps raising: an unconfirmed reclaim must never read as success.

The daemon here is a scripted response sequence, so the concurrent remover is staged
deterministically; ``test_docker_backend.py::test_docker_reclaim_races_a_real_concurrent_remover``
stages the same race against a real daemon wherever docker is available.
"""
import pytest

from runtime_kernel import docker_backend
from runtime_kernel.backend import WorkloadHandle
from runtime_kernel.docker_backend import DockerBackend


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _scripted_backend(monkeypatch, responses):
    """A DockerBackend whose daemon plays back ``responses``, repeating the last one forever
    (a container that lingers keeps answering the same inspect); returns (backend, calls-log).
    Each call is logged as (method, path, timeout-or-None) so the poll's own bound is pinned."""
    script = list(responses)
    calls: list[tuple[str, str, object]] = []

    def fake_req(self, method, path, **kw):
        calls.append((method, path, kw.get("timeout")))
        return script.pop(0) if len(script) > 1 else script[0]

    monkeypatch.setattr(DockerBackend, "_req", fake_req)
    monkeypatch.setattr(docker_backend, "_reclaim_sleep", lambda s: None)
    return DockerBackend(), calls


_H = WorkloadHandle("wl-1", "vexa-wl-1")


def test_cleanup_returns_on_204_and_404(monkeypatch):
    for code in (204, 404):
        be, calls = _scripted_backend(monkeypatch, [_Resp(code)])
        be.cleanup(_H)
        assert calls == [("DELETE", "/containers/vexa-wl-1?force=true", None)]


def test_cleanup_409_confirms_the_concurrent_removers_reclaim(monkeypatch):
    # Staged race: our DELETE loses to another remover (409), the container lingers for one
    # inspect, then is gone. cleanup returns success because ABSENCE was proven — not because
    # our DELETE won.
    be, calls = _scripted_backend(
        monkeypatch,
        [
            _Resp(409, '{"message":"removal of container vexa-wl-1 is already in progress"}'),
            _Resp(200),  # still being removed
            _Resp(404),  # gone
        ],
    )
    be.cleanup(_H)
    # the poll's own 2s bound is part of the contract: _req's default 30s would let one
    # stalled inspect stretch the confirm window far past its deadline
    assert calls == [
        ("DELETE", "/containers/vexa-wl-1?force=true", None),
        ("GET", "/containers/vexa-wl-1/json", 2),
        ("GET", "/containers/vexa-wl-1/json", 2),
    ]


def test_cleanup_409_keys_on_the_status_code_not_the_message(monkeypatch):
    # The branch must never parse the daemon's message: a 409 with an empty or unfamiliar body
    # takes the same confirm-absence path — success still requires the literal 404.
    be, calls = _scripted_backend(monkeypatch, [_Resp(409, ""), _Resp(404)])
    be.cleanup(_H)
    assert calls == [
        ("DELETE", "/containers/vexa-wl-1?force=true", None),
        ("GET", "/containers/vexa-wl-1/json", 2),
    ]


def test_cleanup_409_with_a_container_that_never_leaves_raises(monkeypatch):
    # The bounded window is the truthfulness line: a daemon that claims removal-in-progress
    # while the container stays present must FAIL the reclaim, not report destroyed.
    monkeypatch.setattr(docker_backend, "_reclaim_confirm_sec", lambda: 0.05)
    be, calls = _scripted_backend(
        monkeypatch,
        [
            _Resp(409, '{"message":"removal of container vexa-wl-1 is already in progress"}'),
            _Resp(200),  # repeated forever — the container never leaves
        ],
    )
    with pytest.raises(RuntimeError, match="still present"):
        be.cleanup(_H)
    # the window was spent POLLING, not merely waited out
    assert calls[0] == ("DELETE", "/containers/vexa-wl-1?force=true", None)
    assert ("GET", "/containers/vexa-wl-1/json", 2) in calls


def test_cleanup_409_keeps_polling_through_a_daemon_blip(monkeypatch):
    # A transient inspect failure mid-confirm is not a verdict: the poll continues and the
    # later 404 still resolves the reclaim as success.
    responses = [
        _Resp(409, '{"message":"removal of container vexa-wl-1 is already in progress"}'),
        ConnectionError("daemon restarting"),
        _Resp(404),
    ]
    calls: list[tuple[str, str]] = []

    def fake_req(self, method, path, **kw):
        calls.append((method, path))
        item = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(DockerBackend, "_req", fake_req)
    monkeypatch.setattr(docker_backend, "_reclaim_sleep", lambda s: None)
    DockerBackend().cleanup(_H)
    assert len(calls) == 3


def test_cleanup_409_with_the_daemon_gone_says_so_instead_of_claiming_presence(monkeypatch):
    # When EVERY inspect fails, presence was never observed — the error must say the absence
    # could not be confirmed, not send an operator hunting a phantom "still present" container.
    monkeypatch.setattr(docker_backend, "_reclaim_confirm_sec", lambda: 0.05)
    monkeypatch.setattr(docker_backend, "_reclaim_sleep", lambda s: None)
    first = [True]

    def fake_req(self, method, path, **kw):
        if first[0]:
            first[0] = False
            return _Resp(409, '{"message":"removal of container vexa-wl-1 is already in progress"}')
        raise ConnectionError("daemon gone")

    monkeypatch.setattr(DockerBackend, "_req", fake_req)
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        DockerBackend().cleanup(_H)


def test_cleanup_other_errors_still_raise(monkeypatch):
    be, _calls = _scripted_backend(monkeypatch, [_Resp(500, "daemon exploded")])
    with pytest.raises(RuntimeError, match="daemon exploded"):
        be.cleanup(_H)
