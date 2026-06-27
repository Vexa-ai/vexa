"""Latency + cProfile overhead measurement for the fastapi-guard integration.

Measures, over ASGITransport (no network), per-request latency for:
  A) baseline       — no guard middleware
  B) guard idle     — guard in the stack, rate limiting OFF, Redis OFF
  C) guard enforcing — guard in the stack, per-IP rate limiting ON (limit high
                       so no request is rejected), Redis OFF

Reports mean / p50 / p95 / p99 and the first-request (lazy-init) cost for each,
then a cProfile top-by-cumtime breakdown of the enforcing case.

Run:  ../../.venv-guard/bin/python tests/profile_guard.py
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import statistics
import time

import httpx
from fastapi import FastAPI
from guard import SecurityConfig
from httpx import ASGITransport

from guard_config import apply_guard

os.environ.setdefault("GUARD_ENABLED", "true")
os.environ.setdefault("GUARD_ENABLE_REDIS", "false")

_N = 2000


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        return ordered[min(n - 1, int(round((p / 100.0) * (n - 1))))]

    return {
        "mean": statistics.fmean(samples),
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
    }


async def _root() -> dict[str, str]:
    return {"ok": "true"}


def _make_app(config: SecurityConfig | None) -> FastAPI:
    app = FastAPI()
    app.add_api_route("/", _root, methods=["GET"])
    if config is not None:
        apply_guard(app, config=config)
    return app


def _measure(app: FastAPI, n: int) -> tuple[dict[str, float], float, list[float]]:
    """Return (percentiles_us, first_request_us, all_samples_us)."""
    transport = ASGITransport(app=app)
    samples: list[float] = []
    first_us = 0.0

    async def run() -> None:
        nonlocal first_us
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            t0 = time.perf_counter()
            await ac.get("/")
            first_us = (time.perf_counter() - t0) * 1e6
            for _ in range(n - 1):
                s = time.perf_counter()
                await ac.get("/")
                samples.append((time.perf_counter() - s) * 1e6)

    import asyncio

    asyncio.run(run())
    return _percentiles(samples), first_us, samples


def _format(label: str, pct: dict[str, float], first: float) -> str:
    return (
        f"  {label:<20} first={first:8.1f}us  "
        f"mean={pct['mean']:7.2f}us  p50={pct['p50']:7.2f}us  "
        f"p95={pct['p95']:7.2f}us  p99={pct['p99']:7.2f}us"
    )


def main() -> None:
    idle = SecurityConfig(
        enable_redis=False,
        redis_url=None,
        enable_rate_limiting=False,
        enable_ip_banning=False,
        enable_penetration_detection=False,
        enable_cors=False,
        security_headers={"enabled": False},
        fail_secure=False,
        lazy_init=True,
        exclude_paths=[],
    )
    enforcing = SecurityConfig(
        enable_redis=False,
        redis_url=None,
        enable_rate_limiting=True,
        rate_limit=1_000_000,
        rate_limit_window=60,
        enable_ip_banning=False,
        enable_penetration_detection=False,
        enable_cors=False,
        security_headers={"enabled": False},
        fail_secure=False,
        lazy_init=True,
        exclude_paths=[],
    )

    print(f"Per-request latency over {_N} requests (ASGITransport, microseconds):")
    print("-" * 92)

    base_pct, base_first, _ = _measure(_make_app(None), _N)
    print(_format("baseline (no guard)", base_pct, base_first))

    idle_pct, idle_first, _ = _measure(_make_app(idle), _N)
    print(_format("guard idle (RL off)", idle_pct, idle_first))

    enf_pct, enf_first, _ = _measure(_make_app(enforcing), _N)
    print(_format("guard enforcing (RL on)", enf_pct, enf_first))

    print("-" * 92)
    print("Overhead vs baseline (mean):")
    print(f"  guard idle       +{idle_pct['mean'] - base_pct['mean']:7.2f}us")
    print(f"  guard enforcing  +{enf_pct['mean'] - base_pct['mean']:7.2f}us")
    print(f"  first-request (lazy init): idle={idle_first:.1f}us")
    print(f"  first-request (lazy init): enforcing={enf_first:.1f}us")

    print("\ncProfile top 15 by cumulative time (guard enforcing, 1000 requests):")
    prof = cProfile.Profile()
    app = _make_app(enforcing)
    transport = ASGITransport(app=app)
    import asyncio

    async def profiled() -> None:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get("/")  # warm lazy init outside the profile
            for _ in range(1000):
                await ac.get("/")

    prof.enable()
    asyncio.run(profiled())
    prof.disable()
    stream = io.StringIO()
    stats = pstats.Stats(prof, stream=stream).sort_stats("cumulative")
    stats.print_stats(15)
    # Filter to guard-relevant and FastAPI/httpx frames for signal.
    lines = stream.getvalue().splitlines()
    header = lines[:6]
    guard_lines = [
        ln for ln in lines[6:] if "guard" in ln.lower() or "middleware" in ln.lower()
    ]
    print("\n".join(header))
    print("\n".join(guard_lines[:15]))


if __name__ == "__main__":
    main()
