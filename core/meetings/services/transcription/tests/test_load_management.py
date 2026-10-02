"""MAX_CONCURRENT_TRANSCRIPTIONS device-aware default + TRANSCRIPTION_TIMEOUT_S.

Regression coverage for the 2026-08-13 CPU deadlock incident: a GPU-sized default (20) was
admitted on a CPU-only worker, none of the 20 concurrent jobs finished in bounded time, the
semaphore that gates admission was never released, and the worker answered 200 on /health while
503-ing every /v1/audio/transcriptions request for hours until it was restarted by hand.

Three independent fixes, three independent test groups:
- the concurrency default is device-aware (20 only for an explicit "cuda", the conservative
  cap for everything else - including "auto", which can resolve to CPU at runtime), normalized
  (stripped/lowercased) before comparison, and an operator-set MAX_ACTIVE_REQUESTS/
  MAX_CONCURRENT_TRANSCRIPTIONS still wins over the default either way.
- a bounded per-request timeout on the blocking model.transcribe() call guarantees the semaphore
  permit is always reclaimed, even if that call itself never returns - the concurrency default
  alone does not guarantee this (see the comment on TRANSCRIPTION_TIMEOUT_S).
- the executor is sized with headroom over the semaphore, because reclaiming the permit alone
  does not free up a thread to run the next request in (the reclaimed permit is otherwise
  useless - see the comment on transcription_executor).
"""
from __future__ import annotations

import importlib
import io
import os
import threading
import time
from contextlib import contextmanager

import numpy as np
import soundfile as sf

import transcription.main as svc

_ENV_KEYS = ("DEVICE", "MAX_ACTIVE_REQUESTS", "MAX_CONCURRENT_TRANSCRIPTIONS", "TRANSCRIPTION_TIMEOUT_S")


@contextmanager
def _reloaded_with_env(**env: str):
    """Reload transcription.main with only the given env vars of interest set, then reload again
    on the way out so later tests see the module in its original (ambient-env) state."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(env)
        importlib.reload(svc)
        yield svc
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(svc)


# --- default selection: pure function, no env/import gymnastics needed -----------------------

def test_default_max_concurrent_is_small_on_cpu():
    assert svc._default_max_concurrent("cpu") == 2


def test_default_max_concurrent_is_twenty_on_cuda():
    assert svc._default_max_concurrent("cuda") == 20


def test_default_max_concurrent_treats_unknown_device_as_cpu_like():
    # Inverted on purpose: only the exact literal "cuda" gets the GPU-benchmarked default.
    # "auto" (faster-whisper's runtime-resolved device, which can land on CPU) and anything else
    # unrecognized must NOT silently inherit the GPU default just for not being "cpu".
    assert svc._default_max_concurrent("mps") == 2
    assert svc._default_max_concurrent("auto") == 2


def test_default_max_concurrent_requires_exact_lowercase_cuda():
    # The function itself does no normalization - normalization happens once at DEVICE's
    # assignment (see test_device_env_is_normalized below). A caller that passes an unnormalized
    # string does not get the GPU default.
    assert svc._default_max_concurrent("CUDA") == 2
    assert svc._default_max_concurrent(" cuda ") == 2


# --- the same default, wired end to end through the module's env parsing ----------------------

def test_module_default_is_two_on_cpu():
    with _reloaded_with_env(DEVICE="cpu") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 2


def test_module_default_is_twenty_on_cuda():
    with _reloaded_with_env(DEVICE="cuda") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 20


def test_explicit_env_overrides_default_on_cpu():
    with _reloaded_with_env(DEVICE="cpu", MAX_ACTIVE_REQUESTS="7") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 7


def test_explicit_env_overrides_default_on_cuda():
    with _reloaded_with_env(DEVICE="cuda", MAX_ACTIVE_REQUESTS="3") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 3


def test_legacy_env_name_still_honored():
    # MAX_CONCURRENT_TRANSCRIPTIONS is the back-compat name; MAX_ACTIVE_REQUESTS takes priority
    # when both are set (unchanged precedence, just re-asserted here since the default it falls
    # back to is no longer a bare literal).
    with _reloaded_with_env(DEVICE="cpu", MAX_CONCURRENT_TRANSCRIPTIONS="9") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 9
    with _reloaded_with_env(DEVICE="cpu", MAX_ACTIVE_REQUESTS="5", MAX_CONCURRENT_TRANSCRIPTIONS="9") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 5


def test_module_default_is_two_on_auto():
    # "auto" is not the literal "cuda" - faster-whisper resolves it to whatever CTranslate2 finds
    # at runtime, which can be CPU, so it must get the conservative default, not the GPU one.
    with _reloaded_with_env(DEVICE="auto") as m:
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 2


def test_device_env_is_normalized():
    # Stray casing/whitespace in the operator-set DEVICE must not defeat the "cuda"-only GPU
    # default, and must not defeat the "cpu"-only CPU_THREADS branch either.
    with _reloaded_with_env(DEVICE="CUDA") as m:
        assert m.DEVICE == "cuda"
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 20
    with _reloaded_with_env(DEVICE=" cpu ") as m:
        assert m.DEVICE == "cpu"
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 2


def test_unset_device_still_defaults_to_cuda():
    # Existing GPU deployments that never set DEVICE must keep getting the GPU default.
    with _reloaded_with_env() as m:
        assert m.DEVICE == "cuda"
        assert m.MAX_CONCURRENT_TRANSCRIPTIONS == 20


# --- TRANSCRIPTION_TIMEOUT_S: default + override -----------------------------------------------

def test_timeout_default_is_120s():
    # A hang backstop, not a latency governor - it must sit well above legitimate worst-case
    # work, not under the bot's 30s per-attempt AbortController. Measured on a reference CPU
    # deployment, a full-size (~30s) chunk takes ~28.5-29.0s wall clock on its own; 120s leaves
    # ~4x margin over that. See the comment on TRANSCRIPTION_TIMEOUT_S.
    with _reloaded_with_env(DEVICE="cpu") as m:
        assert m.TRANSCRIPTION_TIMEOUT_S == 120.0


def test_timeout_env_override():
    with _reloaded_with_env(DEVICE="cpu", TRANSCRIPTION_TIMEOUT_S="5") as m:
        assert m.TRANSCRIPTION_TIMEOUT_S == 5.0


# --- the mechanism itself: a call that never returns must not wedge the worker -----------------

class _HangingModel:
    """Stands in for WhisperModel: transcribe() blocks well past the test's timeout budget."""

    def transcribe(self, *_args, **_kwargs):
        time.sleep(0.5)
        return iter(()), type("Info", (), {"language": "en", "language_probability": 1.0})()


def _silent_wav_bytes() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    return buf.getvalue()


def test_hanging_transcription_call_times_out_and_frees_the_slot(client, monkeypatch):
    monkeypatch.setattr(svc, "API_TOKEN", "")
    monkeypatch.setattr(svc, "model", _HangingModel())
    monkeypatch.setattr(svc, "TRANSCRIPTION_TIMEOUT_S", 0.05)

    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", _silent_wav_bytes(), "audio/wav")},
        data={"model": "large-v3-turbo"},
    )

    assert r.status_code == 504
    # The permit must be reclaimed even though the underlying call is still asleep in its
    # worker thread - this is exactly the invariant the 2026-08-13 incident violated.
    assert svc.transcription_semaphore._value == svc.MAX_CONCURRENT_TRANSCRIPTIONS
    assert svc.waiting_requests == 0


# --- the invariant that makes the timeout MEAN something: reclaiming the semaphore permit -----
# must actually buy a runnable thread, not just an accounting fiction. The test above only
# checks the semaphore/waiting-request counters, which a same-sized executor would also satisfy
# while still leaving every request queued behind zombie threads (the reviewer's repro) - these
# tests inspect transcription_executor itself so that regression cannot slip back in unnoticed.

def test_executor_has_headroom_over_the_semaphore():
    # Without at least +1 headroom, reclaiming a semaphore permit is worthless: the zombie
    # thread from a timed-out call still occupies its ThreadPoolExecutor slot (see the
    # transcription_executor comment), so a newly-admitted request just queues behind it.
    assert svc.transcription_executor._max_workers > svc.MAX_CONCURRENT_TRANSCRIPTIONS


def test_a_fresh_job_can_start_while_the_pool_is_full_of_hung_jobs():
    """Direct reproduction of the reviewer's repro against the module's real executor: fill
    every semaphore-sized slot with a job that never returns on its own (standing in for a
    timed-out-but-still-running transcription), then confirm a freshly submitted job still gets
    a thread and runs - instead of queueing behind the hung ones, which is what a 1:1
    executor-to-semaphore ratio produces."""
    release = threading.Event()

    def _hang():
        release.wait(timeout=5)

    hung = [svc.transcription_executor.submit(_hang) for _ in range(svc.MAX_CONCURRENT_TRANSCRIPTIONS)]
    try:
        started = threading.Event()
        svc.transcription_executor.submit(started.set)
        assert started.wait(timeout=1.0), "fresh job queued behind the hung ones instead of starting"
    finally:
        release.set()
        for f in hung:
            f.result(timeout=5)


# --- the CPU-model startup warning: which MODEL_SIZE family it should fire for -----------------
# deploy/transcription/docker-compose.cpu.yml already documents the CPU-safe/unsafe split (its
# own default `small` keeps pace; `medium` sheds load with 503 and gets NO transcript) - the
# heuristic here must match that split, not invent its own, and must not flag `distil-large-v3`
# (a model distilled specifically to be faster) just because it contains the substring "large".

def test_cpu_unsafe_model_size_flags_medium_and_large_family():
    assert svc._is_cpu_unsafe_model_size("medium") is True
    assert svc._is_cpu_unsafe_model_size("medium.en") is True
    assert svc._is_cpu_unsafe_model_size("large-v3-turbo") is True  # this service's own default
    assert svc._is_cpu_unsafe_model_size("large-v2") is True


def test_cpu_unsafe_model_size_does_not_flag_the_compose_files_cpu_safe_default():
    assert svc._is_cpu_unsafe_model_size("small") is False
    assert svc._is_cpu_unsafe_model_size("base") is False
    assert svc._is_cpu_unsafe_model_size("tiny") is False


def test_cpu_unsafe_model_size_does_not_slander_distil_models():
    # distil-large-v3 is distilled specifically to be faster than large-v3 - the measured
    # ~0.95x-realtime figure was never measured against it, so it must not be flagged just
    # because "large" appears as a substring of its name.
    assert svc._is_cpu_unsafe_model_size("distil-large-v3") is False
    assert svc._is_cpu_unsafe_model_size("distil-medium.en") is False


def test_cpu_unsafe_model_size_is_normalized():
    assert svc._is_cpu_unsafe_model_size("LARGE-V3") is True
    assert svc._is_cpu_unsafe_model_size(" medium ") is True
