"""The logo eval (`eval/workspace_identity_eval.py`), runnable from pytest when the network is
allowed: `VEXA_EVAL_NETWORK=1 uv run pytest -q tests/test_workspace_identity_eval.py`."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval/workspace_identity_eval.py"


def _load():
    spec = importlib.util.spec_from_file_location("workspace_identity_eval", EVAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_offline_skips_cleanly(capsys):
    assert _load().main(["--offline"]) == 0
    assert "SKIP" in capsys.readouterr().out


@pytest.mark.skipif(os.environ.get("VEXA_EVAL_NETWORK") != "1", reason="network eval; set VEXA_EVAL_NETWORK=1")
def test_real_sites_logo_found_and_stored_locally():
    assert _load().main([]) == 0
