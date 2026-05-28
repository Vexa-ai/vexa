#!/usr/bin/env python3
"""Seed and validate Lite local-storage canonical recording playback.

The generated API token is kept in memory only. Evidence files receive a
redacted seed payload.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


LITE_CONTAINER = "vexa-0-10-6x-pack-1-recording-playback-trust-lite"
GATEWAY = "http://localhost:42271"
DASHBOARD = "http://localhost:42270"


SEED_CODE = r"""
import asyncio
import io
import json
import random
import sys
import time
import wave
from datetime import datetime, timedelta

sys.path[:0] = ["/app/admin-models", "/app/meeting-api", "/app/schema-sync"]

from admin_models.models import APIToken, User
from admin_models.token_scope import generate_prefixed_token
from meeting_api.database import async_session_local
from meeting_api.models import Meeting
from meeting_api.storage import create_storage_client


def wav_bytes(duration_seconds=0.25, sample_rate=16000):
    frames = int(duration_seconds * sample_rate)
    with io.BytesIO() as buf:
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * frames)
        return buf.getvalue()


async def main():
    now = datetime.utcnow().replace(microsecond=0)
    suffix = f"{int(time.time())}{random.randint(1000, 9999)}"
    rec_id = int(suffix[-9:])
    media_id = rec_id + 1
    token = generate_prefixed_token("tx", 40)
    email = f"pack1-lite-smoke-{suffix}@example.com"
    session_uid = f"pack1-lite-smoke-{suffix}"

    async with async_session_local() as db:
        user = User(
            email=email,
            name="PACK 1 Lite Smoke",
            max_concurrent_bots=1,
            data={"pack": "1"},
        )
        db.add(user)
        await db.flush()

        db.add(
            APIToken(
                token=token,
                user_id=user.id,
                scopes=["tx"],
                name="PACK 1 Lite playback smoke",
            )
        )

        storage_path = f"recordings/{user.id}/{rec_id}/{session_uid}/audio/master.wav"
        audio = wav_bytes()
        create_storage_client("local").upload_file(
            storage_path,
            audio,
            content_type="audio/wav",
        )

        meeting = Meeting(
            user_id=user.id,
            platform="google_meet",
            platform_specific_id=f"pack1-lite-{suffix}",
            status="completed",
            start_time=now - timedelta(seconds=30),
            end_time=now,
            data={},
        )
        db.add(meeting)
        await db.flush()
        meeting.data = {
            "recordings": [
                {
                    "id": rec_id,
                    "meeting_id": meeting.id,
                    "session_uid": session_uid,
                    "source": "bot",
                    "status": "completed",
                    "created_at": (now - timedelta(seconds=30)).isoformat() + "Z",
                    "completed_at": now.isoformat() + "Z",
                    "playback_url": {
                        "audio": f"/recordings/{rec_id}/master?type=audio",
                        "video": None,
                    },
                    "media_files": [
                        {
                            "id": media_id,
                            "type": "audio",
                            "format": "wav",
                            "storage_path": storage_path,
                            "storage_backend": "local",
                            "file_size_bytes": len(audio),
                            "duration_seconds": 0.25,
                            "finalized_by": "recording_finalizer.master",
                            "is_final": True,
                        }
                    ],
                }
            ]
        }
        await db.commit()

    print(
        json.dumps(
            {
                "token": token,
                "recording_id": rec_id,
                "media_file_id": media_id,
                "storage_backend": "local",
                "storage_path": storage_path,
                "expected_duration_seconds": 0.25,
                "expected_size_bytes": len(audio),
            }
        )
    )


asyncio.run(main())
"""


def run(args: list[str], *, stdin: str | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        check=False,
    )


def fail(message: str, proc: subprocess.CompletedProcess | None = None) -> None:
    if proc is not None and proc.stderr:
        message = f"{message}\n{proc.stderr.strip()}"
    raise SystemExit(message)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    ns = parser.parse_args()

    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)

    status = run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={LITE_CONTAINER}",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Ports}}",
        ]
    )
    if status.returncode != 0:
        fail("failed to inspect Lite container", status)
    write_text(out / "service-status.txt", status.stdout)

    root = run(["curl", "-fsS", f"{GATEWAY}/"])
    if root.returncode != 0:
        fail("gateway root check failed", root)
    write_text(out / "gateway-root.json", root.stdout)

    dashboard = run(["curl", "-fsSI", f"{DASHBOARD}/"])
    if dashboard.returncode != 0:
        fail("dashboard head check failed", dashboard)
    write_text(out / "dashboard.headers", dashboard.stdout)

    seeded = run(["docker", "exec", "-i", LITE_CONTAINER, "python3", "-"], stdin=SEED_CODE)
    if seeded.returncode != 0:
        fail("Lite seed failed", seeded)

    try:
        seed = json.loads(seeded.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Lite seed returned invalid JSON: {exc}") from exc

    token = seed["token"]
    rec_id = str(seed["recording_id"])
    media_id = str(seed["media_file_id"])
    redacted_seed = dict(seed)
    redacted_seed["token"] = "[redacted-generated-lite-token]"
    write_text(out / "seed.json", json.dumps(redacted_seed, indent=2, sort_keys=True) + "\n")

    master_path = out / "master.json"
    master = run(
        [
            "curl",
            "-sS",
            "-o",
            str(master_path),
            "-w",
            "%{http_code}",
            "-H",
            f"X-API-Key: {token}",
            f"{GATEWAY}/recordings/{rec_id}/master?type=audio",
        ]
    )
    if master.returncode != 0:
        fail("master endpoint request failed", master)
    write_text(out / "master.http_status", master.stdout + "\n")
    if master.stdout != "200":
        fail(f"master endpoint returned {master.stdout}")

    master_payload = json.loads(master_path.read_text(encoding="utf-8"))
    expected_raw = f"/recordings/{rec_id}/media/{media_id}/raw"
    assert master_payload["url"] == expected_raw, master_payload
    assert master_payload["download_url"] == expected_raw, master_payload
    assert master_payload["raw_url"] == expected_raw, master_payload
    assert master_payload["media_file_id"] == int(media_id), master_payload
    assert master_payload["content_type"] == "audio/wav", master_payload
    assert abs(float(master_payload["duration_seconds"]) - 0.25) < 0.001, master_payload

    headers_path = out / "raw-range.headers"
    raw_path = out / "raw-range.bin"
    raw = run(
        [
            "curl",
            "-sS",
            "-D",
            str(headers_path),
            "-o",
            str(raw_path),
            "-w",
            "%{http_code}",
            "-H",
            f"X-API-Key: {token}",
            "-H",
            "Range: bytes=0-15",
            f"{GATEWAY}/recordings/{rec_id}/media/{media_id}/raw",
        ]
    )
    if raw.returncode != 0:
        fail("raw range request failed", raw)
    write_text(out / "raw-range.http_status", raw.stdout + "\n")
    if raw.stdout != "206":
        fail(f"raw range endpoint returned {raw.stdout}")
    if raw_path.stat().st_size != 16:
        fail(f"raw range byte count was {raw_path.stat().st_size}, expected 16")

    headers = headers_path.read_text(encoding="utf-8", errors="replace").lower()
    assert "content-range:" in headers, headers
    assert "accept-ranges: bytes" in headers, headers

    print(f"Lite playback smoke passed for recording {rec_id} media {media_id} on gateway port 42271")
    return 0


if __name__ == "__main__":
    sys.exit(main())
