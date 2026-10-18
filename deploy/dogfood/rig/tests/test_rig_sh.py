"""Finding 3 (live, 2026-09-03) — `rig.sh down` killed a bystander.

`down` ran `pkill -f vexa_control_mcp`, which matches any process whose COMMAND LINE contains that
string. Stage-1 was connected over ssh with the path in its own command line, so stopping the rig
also killed the session that was stopping it. `-f` matching is not process identity; it is a
substring search over other people's arguments.

Finding 2's other half is here too: the rig has never had a venv, so `rig.sh` ran it out of a
stale, shared checkout's one.

These tests drive the script's own functions in bash rather than reading it, because the defect
was behavioural: the string was right there in plain sight and looked fine.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import time

RIG_SH = pathlib.Path(__file__).resolve().parents[1] / "rig.sh"


def _bash(script: str, **env) -> subprocess.CompletedProcess:
    """Source rig.sh as a LIBRARY (no dispatch) and run `script` against its functions."""
    return subprocess.run(
        ["bash", "-c", f'set -u; RIG_SH_LIB=1 source "{RIG_SH}"\n{script}'],
        capture_output=True, text=True,
        env={**os.environ, "HOME": env.pop("HOME", os.environ["HOME"]), **env})


def test_the_stop_path_does_not_pattern_match_command_lines():
    # EXECUTABLE lines only. The comment above the fix quotes the old command deliberately —
    # a reader who does not know what was there cannot tell why the pidfile is worth the code.
    code = [ln for ln in RIG_SH.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    offending = [ln for ln in code if "pkill" in ln]
    assert offending == [], (
        "`pkill -f` matched stage-1's own ssh command line and killed it. A pidfile names one "
        f"process; a substring names whoever happens to be typing. Still here: {offending}")


def test_stop_kills_the_rig_and_leaves_a_bystander_alone():
    """THE LIVE BUG, reproduced: a bystander whose command line contains `vexa_control_mcp.py`.

    Here it is a `sleep` given that argument — which is exactly the shape stage-1's ssh session had.
    """
    state = pathlib.Path(tempfile.mkdtemp(prefix="rig-sh-"))
    out = _bash(f'''
        RUN_DIR="{state}"
        # the rig itself — command line exactly the shape rig.sh starts, pid recorded
        bash -c 'exec -a "python -u /home/dima/.storm/vexa_control_mcp.py" sleep 30' & RIG_PID=$!
        echo $RIG_PID > "$RUN_DIR/control-mcp.pid"
        # the bystander: stage-1's ssh session, whose command line ALSO carries the path.
        # Nothing about the two strings tells them apart — only the pid does.
        bash -c 'exec -a "ssh bbb tail -f vexa_control_mcp.py" sleep 30' & BYSTANDER=$!
        sleep 0.3
        stop_by_pidfile "$RUN_DIR/control-mcp.pid" >/dev/null 2>&1
        sleep 0.5
        kill -0 $RIG_PID 2>/dev/null && echo "RIG_ALIVE" || echo "RIG_DEAD"
        kill -0 $BYSTANDER 2>/dev/null && echo "BYSTANDER_ALIVE" || echo "BYSTANDER_DEAD"
        kill $BYSTANDER 2>/dev/null
        [ -e "$RUN_DIR/control-mcp.pid" ] && echo "PIDFILE_KEPT" || echo "PIDFILE_CLEARED"
    ''')
    assert "RIG_DEAD" in out.stdout, out
    assert "BYSTANDER_ALIVE" in out.stdout, (
        "the bystander died — this is the live incident, reproduced", out.stdout, out.stderr)
    assert "PIDFILE_CLEARED" in out.stdout, out.stdout


def test_stop_is_safe_when_the_pid_has_been_reused_or_is_gone():
    """A stale pidfile must not kill whatever now holds that number. The recorded pid is checked
    against the command line before the signal — belt for the pidfile's braces."""
    state = pathlib.Path(tempfile.mkdtemp(prefix="rig-sh-"))
    out = _bash(f'''
        RUN_DIR="{state}"
        bash -c 'exec -a "some other program entirely" sleep 30' & OTHER=$!
        echo $OTHER > "$RUN_DIR/control-mcp.pid"
        sleep 0.3
        stop_by_pidfile "$RUN_DIR/control-mcp.pid" >/dev/null 2>&1
        sleep 0.3
        kill -0 $OTHER 2>/dev/null && echo "OTHER_ALIVE" || echo "OTHER_DEAD"
        kill $OTHER 2>/dev/null
        echo "---"
        echo 999999 > "$RUN_DIR/control-mcp.pid"
        stop_by_pidfile "$RUN_DIR/control-mcp.pid" >/dev/null 2>&1; echo "exit=$?"
    ''')
    assert "OTHER_ALIVE" in out.stdout, ("a reused pid was killed", out.stdout, out.stderr)
    assert "exit=0" in out.stdout, ("a stale pidfile must not fail the stop", out.stdout)


def test_the_rig_runs_out_of_its_own_venv():
    body = RIG_SH.read_text()
    assert "$RIG_DIR/.venv" in body, \
        "rig.sh still borrows another checkout's venv (finding 2)"
    assert "pyproject.toml" in body, "the venv is not built from the rig's own declaration"
    # The interpreter the server is launched with must come from the rig's venv, not from $FL's.
    launch = [ln for ln in body.splitlines() if "$CTL" in ln and "python" in ln]
    assert launch, "cannot find the line that launches the server"
    assert all("VENV" not in ln or "RIG" in ln for ln in launch), launch
