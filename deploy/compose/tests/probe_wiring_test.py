"""The `make probe` wiring proof (offline — no stack, no docker).

`make probe SURFACE=<lite|compose|helm>` is the standing hot-loop entry point: the
full-journey smoke (spawn→schedule→boot→join→transcribe→live-view→stop + one-shot log
sweep) an agent or operator drops a hypothesis into. These assertions pin the WIRING —
the entry point exists per surface, delegates like `make all`/`make lite` do, and every
per-surface script is a well-formed executable — so the journey itself (proved live,
per surface) can never silently lose its front door.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SURFACES = ("compose", "lite", "helm")


def test_root_makefile_has_probe_target():
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(r"^probe:", makefile, re.M), "root Makefile: no `probe` target"
    assert "deploy/$(SURFACE)/probe.sh" in makefile, "probe target must delegate per surface"
    assert re.search(r"^SURFACE \?= compose$", makefile, re.M), "compose is the fast default surface"
    assert "make probe" in makefile, "`make help` must list probe"


def test_per_surface_probe_scripts_exist_and_parse():
    for surface in SURFACES:
        script = ROOT / "deploy" / surface / "probe.sh"
        assert script.is_file(), f"deploy/{surface}/probe.sh missing"
        assert script.stat().st_mode & 0o111, f"deploy/{surface}/probe.sh not executable"
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_shared_journey_core_exists_and_parses():
    journey = ROOT / "scripts" / "probe" / "journey.sh"
    assert journey.is_file() and journey.stat().st_mode & 0o111
    subprocess.run(["bash", "-n", str(journey)], check=True)
    # every wrapper funnels into the ONE shared journey (one contract, per-surface fan-out)
    for surface in SURFACES:
        text = (ROOT / "deploy" / surface / "probe.sh").read_text()
        assert "scripts/probe/journey.sh" in text, f"deploy/{surface}/probe.sh must run the shared journey"
    # the WS listener reuses THIS suite's stdlib client (_ws.py) — no new client invented
    ws_tail = ROOT / "scripts" / "probe" / "ws_tail.py"
    assert ws_tail.is_file()
    assert "from _ws import WS" in ws_tail.read_text()


def test_probe_requires_gateway_and_key():
    """journey.sh fails FAST (exit 1, named missing env) rather than probing nothing."""
    r = subprocess.run(
        ["bash", str(ROOT / "scripts" / "probe" / "journey.sh")],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
    )
    assert r.returncode != 0
    assert "GATEWAY_URL" in (r.stderr + r.stdout)
