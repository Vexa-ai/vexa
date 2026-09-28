"""Exercise the Lite storage warning through make, without Docker."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("contents,endpoint", [
    ("S3_ENDPOINT=https://s3.example.com\n", "https://s3.example.com"),
    ("MINIO_ENDPOINT=minio:9000\n", None),
    ("S3_ENDPOINT=\n", None),
    ("S3_ENDPOINT=https://s3.example.com   # mine\n", "https://s3.example.com"),
])
def test_storage_where(tmp_path, contents, endpoint):
    env_file = tmp_path / ".env"
    env_file.write_text(contents)
    result = subprocess.run(
        ["make", "-s", "-C", str(Path(__file__).parents[1]), "storage-where", f"ENV_FILE={env_file}"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    expected = "" if endpoint is None else (
        f"WARNING: recordings go to {endpoint} (S3_ENDPOINT in {env_file}), "
        "not the bundled storage (vexa-lite-storage). Expected if that is your own S3; "
        "if this install was upgraded from MinIO, see https://docs.vexa.ai/upgrade-from-minio\n"
    )
    assert result.stdout == expected
