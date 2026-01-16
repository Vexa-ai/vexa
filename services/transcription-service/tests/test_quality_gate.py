from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_quality_smoke_gate():
    api_url = os.getenv("TRANSCRIPTION_API_URL")
    if not api_url:
        pytest.skip("Set TRANSCRIPTION_API_URL to run quality gate (e.g. http://localhost:8083/v1/audio/transcriptions)")

    svc_dir = Path(__file__).resolve().parent
    dataset_dir = svc_dir / "quality_dataset"
    manifest = dataset_dir / "manifest.jsonl"
    if not manifest.exists():
        pytest.skip("Quality dataset not generated. Run: python3 -m tests.quality.dataset_generate")

    cmd = [
        sys.executable,
        "-m",
        "tests.quality.run_quality",
        "--api-url",
        api_url,
        "--dataset-dir",
        str(dataset_dir),
        "--languages",
        "en",
        "es",
        "fr",
        "de",
        "it",
        "pt",
        "ru",
        "--max-cases",
        "1",
        "--run-name",
        "pytest_smoke",
    ]
    subprocess.run(cmd, check=True)














