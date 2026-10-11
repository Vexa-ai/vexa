"""Recording round trip through a Helm release's API — run INSIDE the meeting-api pod.

    kubectl -n vexa exec -i deploy/vexa-vexa-meeting-api -c meeting-api -- \
      env GATEWAY_URL=http://vexa-vexa-gateway:8000 python3 - all < recording_roundtrip.py

Phases (argv[1]): `write` (upload 3 chunks the way a bot does → finalize → play back full + ranged
through the gateway), `verify STATE` (play back again, byte-compare), `delete STATE` (DELETE through
the gateway, then the prefix must be empty), `all` (the three in order). `write` prints one
`STATE {...}` line that the later phases take. Every step also lists the recording's prefix in the
bucket with the pod's own storage settings, so the output shows where the objects are.

It uses only what the pod already has: the admin token, the database and the storage env the chart
set (S3_* / MINIO_* / AWS_CONFIG_FILE), asyncpg and boto3 from the meeting-api image. A failed HTTP
step prints `FAIL <step> <status> <body>` and exits 1.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid

GATEWAY = os.environ.get("GATEWAY_URL", "http://vexa-vexa-gateway:8000").rstrip("/")
ADMIN = os.environ["ADMIN_API_URL"].rstrip("/")
MEETING_API = "http://127.0.0.1:8080"
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]


def req(method, url, headers=None, body=None):
    r = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def must(step, want, got):
    status, _, body = got
    if status not in want:
        print(f"FAIL {step} {status} {body[:300]!r}", flush=True)
        sys.exit(1)
    return got


def bucket_keys(prefix):
    """The recording's objects, listed with the pod's own storage settings (same precedence as
    meeting-api: S3_* first, then MINIO_*)."""
    import boto3

    ep = os.environ.get("S3_ENDPOINT")
    if not ep:
        ep = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        if not ep.startswith(("http://", "https://")):
            secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
            ep = ("https://" if secure else "http://") + ep
    c = boto3.client(
        "s3", endpoint_url=ep,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY") or os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY") or os.environ.get("MINIO_SECRET_KEY"),
    )
    bucket = os.environ.get("MINIO_BUCKET") or os.environ.get("RECORDING_BUCKET", "vexa")
    out = c.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return ep, bucket, sorted(o["Key"] for o in out.get("Contents", []))


def api_key(user_id):
    _, _, b = must("mint-api-key", (201,), req(
        "POST", f"{ADMIN}/admin/users/{user_id}/tokens?scopes=bot,tx",
        {"X-Admin-API-Key": ADMIN_TOKEN}, b""))
    return json.loads(b)["token"]


def meeting_token(meeting_id, user_id, native_id):
    """A MeetingToken as meeting-api mints it for a bot (HS256 over ADMIN_TOKEN)."""
    def b64(x):
        return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    now = int(time.time())
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"meeting_id": meeting_id, "user_id": user_id, "platform": "google_meet",
              "native_meeting_id": native_id, "scope": "transcribe:write", "iss": "meeting-api",
              "aud": "transcription-collector", "iat": now, "exp": now + 3600,
              "jti": str(uuid.uuid4())}
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(ADMIN_TOKEN.encode(), f"{head}.{body}".encode(), "sha256").digest()
    return f"{head}.{body}.{b64(sig)}"


async def sql(query, *args):
    import asyncpg

    conn = await asyncpg.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"])
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


def wav(pcm):
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm)


def multipart(fields, data):
    b = "----rt" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="c.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def play(state, key):
    """Finalize + full and ranged reads through the gateway; returns the master's sha256."""
    h = {"x-api-key": key}
    rid, mfid = state["recording_id"], state["media_file_id"]
    _, _, b = must("finalize", (200,), req("GET", f"{GATEWAY}/recordings/{rid}/master?type=audio", h))
    master = json.loads(b)["storage_path"]
    raw = f"{GATEWAY}/recordings/{rid}/media/{mfid}/raw?type=audio"
    _, _, full = must("playback-full", (200,), req("GET", raw, h))
    _, hd, part = must("playback-range", (206,), req("GET", raw, {**h, "Range": "bytes=0-99"}))
    assert part == full[:100], "ranged read differs from the full read"
    print(f"playback: master={master} bytes={len(full)} range 0-99 -> 206 "
          f"{hd.get('content-range') or hd.get('Content-Range')}", flush=True)
    return hashlib.sha256(full).hexdigest()


def write():
    _, _, b = must("create-user", (200, 201), req(
        "POST", f"{ADMIN}/admin/users",
        {"X-Admin-API-Key": ADMIN_TOKEN, "Content-Type": "application/json"},
        json.dumps({"email": f"rt-{uuid.uuid4().hex[:8]}@example.com", "name": "rt",
                    "max_concurrent_bots": 1}).encode()))
    uid = json.loads(b)["id"]
    nid = f"rt-{uuid.uuid4().hex[:8]}"
    mid = asyncio.run(sql(
        "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
        "VALUES ($1, 'google_meet', $2, 'active', '{}'::jsonb) RETURNING id", uid, nid))
    suid = str(uuid.uuid4())
    asyncio.run(sql("INSERT INTO meeting_sessions (meeting_id, session_uid) VALUES ($1, $2) "
                    "RETURNING meeting_id", mid, suid))
    tok = meeting_token(mid, uid, nid)
    for seq, n in enumerate((64000, 64000, 32000)):
        body, ctype = multipart({"session_uid": suid, "media_type": "audio", "media_format": "wav",
                                 "chunk_seq": str(seq), "is_final": "true" if seq == 2 else "false"},
                                wav(bytes([seq + 1]) * n))
        _, _, r = must(f"upload-chunk-{seq}", (200,), req(
            "POST", f"{MEETING_API}/internal/recordings/upload",
            {"Authorization": f"Bearer {tok}", "Content-Type": ctype}, body))
        r = json.loads(r)
    state = {"user_id": uid, "meeting_id": mid, "recording_id": r["recording_id"],
             "media_file_id": r["media_file_id"]}
    print(f"upload: 3 chunks -> 200 recording_id={state['recording_id']}", flush=True)
    state["sha256"] = play(state, api_key(uid))
    ep, bucket, keys = bucket_keys(f"recordings/{uid}/{state['recording_id']}/")
    print(f"bucket: {ep} {bucket} {len(keys)} objects {keys}", flush=True)
    assert len(keys) == 4 and any(k.endswith("master.wav") for k in keys), keys
    print("STATE " + json.dumps(state), flush=True)
    return state


def verify(state):
    got = play(state, api_key(state["user_id"]))
    print(f"verify: sha256 {'MATCH' if got == state['sha256'] else 'DIFFERS'} {got[:16]}", flush=True)
    if got != state["sha256"]:
        sys.exit(1)
    ep, bucket, keys = bucket_keys(f"recordings/{state['user_id']}/{state['recording_id']}/")
    print(f"bucket: {ep} {bucket} {len(keys)} objects {keys}", flush=True)


def delete(state):
    key = api_key(state["user_id"])
    asyncio.run(sql("UPDATE meetings SET status='completed' WHERE id=$1 RETURNING id",
                    state["meeting_id"]))
    _, _, b = must("delete", (200,), req(
        "DELETE", f"{GATEWAY}/recordings/{state['recording_id']}", {"x-api-key": key}))
    print(f"delete: 200 {b.decode()[:200]}", flush=True)
    must("get-after-delete", (404,), req(
        "GET", f"{GATEWAY}/recordings/{state['recording_id']}", {"x-api-key": key}))
    ep, bucket, keys = bucket_keys(f"recordings/{state['user_id']}/{state['recording_id']}/")
    print(f"bucket after delete: {ep} {bucket} {len(keys)} objects {keys}", flush=True)
    assert keys == [], keys


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase == "write":
        write()
    elif phase == "verify":
        verify(json.loads(sys.argv[2]))
    elif phase == "delete":
        delete(json.loads(sys.argv[2]))
    else:
        s = write()
        verify(s)
        delete(s)
    print(f"RESULT {phase} ok", flush=True)
