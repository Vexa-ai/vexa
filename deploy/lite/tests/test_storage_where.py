"""Exercise the Lite storage warning through make, without Docker."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("contents,endpoint", [
    ("S3_ENDPOINT=https://s3.example.com\n", "https://s3.example.com"),
    ("MINIO_ENDPOINT=minio:9000\n", None),
    ("S3_ENDPOINT=\n", None),
    ("S3_ENDPOINT=https://s3.example.com   # mine\n", "https://s3.example.com   # mine"),
    ("  S3_ENDPOINT=http://vexa-lite-minio:9000\n", "http://vexa-lite-minio:9000"),
    ("S3_ENDPOINT=\nS3_ENDPOINT=https://s3.example.com\n", "https://s3.example.com"),
    ("S3_ENDPOINT=https://s3.example.com\nS3_ENDPOINT=\n", None),
    ("S3_ENDPOINT=http://vexa-lite-storage:9000\n", None),
    ("S3_ENDPOINT=http://VEXA-LITE-STORAGE:9000\n", None),
    ("S3_ENDPOINT=https://s3.example.com\r\n", "https://s3.example.com"),
])
@pytest.mark.parametrize("use_makefile_flag", [False, True])
def test_storage_where(tmp_path, contents, endpoint, use_makefile_flag):
    env_file = tmp_path / ".env"
    env_file.write_text(contents)
    makefile_args = ["-f", "deploy/lite/Makefile"] if use_makefile_flag else [
        "-C", str(Path(__file__).parents[1]),
    ]
    result = subprocess.run(
        ["make", "-s", *makefile_args, "storage-where", f"ENV_FILE={env_file}"],
        cwd=Path(__file__).resolve().parents[3],
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
