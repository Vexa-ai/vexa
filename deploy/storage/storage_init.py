#!/usr/bin/env python3
"""Prepare the S3 store meeting-api writes to: the one-shot init for Lite and Compose storage.

Runs once per bring-up inside an image that already carries boto3 (meeting-api in Compose, the
Lite image in Lite), so it ships no extra image. Idempotent: re-running it changes nothing.

1. Resolve the endpoint meeting-api will use, the way meeting-api resolves it: S3_ENDPOINT, else
   MINIO_ENDPOINT (host:port) with MINIO_SECURE.
2. That endpoint is this stack's storage service (STORAGE_ENDPOINT) -> create the bucket if it is
   missing.
   It is the old MinIO service name (LEGACY_MINIO_HOST, default `minio`) -> the install was upgraded
   but its .env still points at the MinIO it used to run. While that MinIO still answers, warn and
   carry on (recordings keep working; nothing is split). When it does not answer, stop with the
   upgrade steps: a stack that starts writing recordings to a store that is not there loses them.
   Anything else is the operator's own S3: check that the bucket answers; never create or change it.
3. Authenticated bots configured against this storage (BOT_S3_ENDPOINT on the storage host, with
   BOT_S3_ACCESS_KEY / BOT_S3_SECRET_KEY / BOT_USERDATA_S3_PATH) -> create or update that scoped
   account, attach a bucket policy that limits it to its own prefix, and prove the limit: its own
   prefix is readable and writable, everything else in the bucket is denied.

Exit codes: 0 ready (or a warning printed) - 1 the stack must not start (message says why).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError

UPGRADE_DOC = "https://docs.vexa.ai/upgrade-from-minio"


def say(msg: str) -> None:
    print(f"[storage-init] {msg}", flush=True)


def endpoint_meeting_api_uses() -> str:
    explicit = os.getenv("S3_ENDPOINT", "").strip()
    if explicit:
        return explicit
    ep = os.getenv("MINIO_ENDPOINT", "").strip()
    if ep.startswith(("http://", "https://")):
        return ep
    scheme = "https" if os.getenv("MINIO_SECURE", "false").strip().lower() == "true" else "http"
    return f"{scheme}://{ep}"


def hostport(url: str) -> tuple[str, int]:
    u = urlparse(url)
    return (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)


def client(endpoint: str, access: str, secret: str):
    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(retries={"max_attempts": 2}, connect_timeout=5, read_timeout=30),
    )


def code_of(e: Exception) -> str:
    if isinstance(e, ClientError):
        return e.response.get("Error", {}).get("Code", "") or str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return type(e).__name__


def wait_ready(c, what: str, seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while True:
        try:
            c.list_buckets()
            return True
        except (ClientError, BotoCoreError) as e:
            if time.time() > deadline:
                say(f"{what} did not answer within {seconds}s ({code_of(e)})")
                return False
            time.sleep(2)


def ensure_bucket(c, bucket: str) -> str:
    try:
        c.head_bucket(Bucket=bucket)
        return "present"
    except ClientError as e:
        if code_of(e) not in ("404", "NoSuchBucket", "NotFound"):
            raise
    try:
        c.create_bucket(Bucket=bucket)
        return "created"
    except ClientError as e:
        if code_of(e) in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            return "present"
        raise


# versitygw admin API: PATCH requests on the S3 port, SigV4-signed with the root key, XML bodies.
def admin(endpoint: str, access: str, secret: str, path: str, body: bytes) -> tuple[int, str]:
    url = endpoint.rstrip("/") + path
    req = AWSRequest(method="PATCH", url=url, data=body,
                     headers={"X-Amz-Content-Sha256": hashlib.sha256(body).hexdigest()})
    SigV4Auth(Credentials(access, secret), "s3", os.getenv("STORAGE_REGION", "us-east-1")).add_auth(req)
    r = urllib.request.Request(url, data=body, method="PATCH", headers=dict(req.headers.items()))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def ensure_scoped_account(endpoint: str, root_ak: str, root_sk: str, ak: str, sk: str) -> str:
    x = lambda v: escape(v)  # noqa: E731
    body = (f"<Account><Access>{x(ak)}</Access><Secret>{x(sk)}</Secret><Role>user</Role>"
            "<UserID>0</UserID><GroupID>0</GroupID><ProjectID>0</ProjectID></Account>").encode()
    status, text = admin(endpoint, root_ak, root_sk, "/create-user", body)
    if status in (200, 201):
        return "created"
    if "exist" not in text.lower():
        raise RuntimeError(f"create-user answered {status}: {text[:300]}")
    body = f"<MutableProps><Secret>{x(sk)}</Secret><Role>user</Role></MutableProps>".encode()
    status, text = admin(endpoint, root_ak, root_sk, f"/update-user?access={quote(ak, safe='')}", body)
    if status not in (200, 201, 204):
        raise RuntimeError(f"update-user answered {status}: {text[:300]}")
    return "updated"


def userdata_policy(bucket: str, prefix: str, principal: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "VexaBotUserdataList", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"],
             "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*"]}}},
            {"Sid": "VexaBotUserdataObjects", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": [f"arn:aws:s3:::{bucket}/{prefix}/*"]},
        ],
    }


def prove_scope(endpoint: str, ak: str, sk: str, bucket: str, prefix: str, root) -> list[str]:
    """Own prefix: put/get/list/delete allowed. A real object under recordings/ and the bucket root: denied."""
    c = client(endpoint, ak, sk)
    other = "recordings/.vexa-scope-check"
    root.put_object(Bucket=bucket, Key=other, Body=b"not yours")
    probe = f"{prefix}/.vexa-scope-check"
    bad: list[str] = []

    def allowed(label, fn):
        try:
            fn()
        except ClientError as e:
            bad.append(f"{label} was denied ({code_of(e)}) but must be allowed")

    def denied(label, fn):
        try:
            fn()
            bad.append(f"{label} was allowed but must be denied")
        except ClientError:
            pass

    allowed("put own prefix", lambda: c.put_object(Bucket=bucket, Key=probe, Body=b"ok"))
    allowed("get own prefix", lambda: c.get_object(Bucket=bucket, Key=probe)["Body"].read())
    allowed("list own prefix", lambda: c.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/"))
    allowed("delete own prefix", lambda: c.delete_object(Bucket=bucket, Key=probe))
    denied("list recordings/", lambda: c.list_objects_v2(Bucket=bucket, Prefix="recordings/"))
    denied("list the whole bucket", lambda: c.list_objects_v2(Bucket=bucket))
    denied("get an object under recordings/", lambda: c.get_object(Bucket=bucket, Key=other)["Body"].read())
    denied("overwrite an object under recordings/", lambda: c.put_object(Bucket=bucket, Key=other, Body=b"x"))
    denied("delete an object under recordings/", lambda: c.delete_object(Bucket=bucket, Key=other))
    if root.get_object(Bucket=bucket, Key=other)["Body"].read() != b"not yours":
        bad.append("an object under recordings/ changed during the check")
    root.delete_object(Bucket=bucket, Key=other)
    return bad


def main() -> int:
    storage_ep = os.getenv("STORAGE_ENDPOINT", "http://storage:9000").strip()
    legacy_host = os.getenv("LEGACY_MINIO_HOST", "minio").strip().lower()
    ep = endpoint_meeting_api_uses()
    access = os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY", "")
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY", "")
    bucket = os.getenv("MINIO_BUCKET", "vexa")
    host = hostport(ep)[0]

    if hostport(ep) == hostport(storage_ep):
        c = client(ep, access, secret)
        if not wait_ready(c, f"storage at {ep}"):
            return 1
        say(f"bucket '{bucket}' {ensure_bucket(c, bucket)} on {ep}")
    elif legacy_host and host == legacy_host:
        c = client(ep, access, secret)
        try:
            c.head_bucket(Bucket=bucket)
            say(f"WARNING: meeting-api still stores recordings in the old MinIO at {ep} (MINIO_ENDPOINT in .env). "
                f"This stack no longer runs MinIO. Copy the recordings into the new storage and switch: {UPGRADE_DOC}")
        except (ClientError, BotoCoreError) as e:
            say(f"STOP: .env points meeting-api at the old MinIO ({ep}), which does not answer ({code_of(e)}). "
                "This stack no longer runs MinIO, so recordings would have nowhere to go. Nothing was changed.")
            say(f"  Keep your recordings: follow {UPGRADE_DOC} (copy them from the old MinIO volume, then set "
                f"MINIO_ENDPOINT={urlparse(storage_ep).netloc}).")
            say(f"  Fresh start without the old recordings: set MINIO_ENDPOINT={urlparse(storage_ep).netloc} in .env.")
            return 1
    else:
        c = client(ep, access, secret)
        try:
            c.head_bucket(Bucket=bucket)
            say(f"meeting-api uses your own S3 at {ep}; bucket '{bucket}' answers. The storage service is unused.")
        except (ClientError, BotoCoreError) as e:
            say(f"WARNING: meeting-api uses your own S3 at {ep}, but bucket '{bucket}' does not answer ({code_of(e)}).")

    bot_ep = os.getenv("BOT_S3_ENDPOINT", "").strip()
    bot_ak, bot_sk = os.getenv("BOT_S3_ACCESS_KEY", "").strip(), os.getenv("BOT_S3_SECRET_KEY", "").strip()
    if not (bot_ep and bot_ak and bot_sk) or hostport(bot_ep)[0] != hostport(storage_ep)[0]:
        return 0
    prefix = os.getenv("BOT_USERDATA_S3_PATH", "").strip().strip("/")
    bot_bucket = os.getenv("BOT_S3_BUCKET", "").strip() or bucket
    root_ak, root_sk = os.getenv("MINIO_ACCESS_KEY", ""), os.getenv("MINIO_SECRET_KEY", "")
    if not prefix:
        say("STOP: BOT_S3_* point at this storage but BOT_USERDATA_S3_PATH is empty; the scoped account needs its own prefix.")
        return 1
    if bot_ak == root_ak:
        say("STOP: BOT_S3_ACCESS_KEY is the storage root key. Authenticated bots need their own scoped key pair.")
        return 1
    rc = client(storage_ep, root_ak, root_sk)
    if not wait_ready(rc, f"storage at {storage_ep}"):
        return 1
    say(f"bucket '{bot_bucket}' {ensure_bucket(rc, bot_bucket)} for bot userdata")
    say(f"scoped userdata account {ensure_scoped_account(storage_ep, root_ak, root_sk, bot_ak, bot_sk)}")
    rc.put_bucket_policy(Bucket=bot_bucket, Policy=json.dumps(userdata_policy(bot_bucket, prefix, bot_ak)))
    bad = []
    for _ in range(10):  # the gateway caches IAM for a short while; give a fresh account a moment
        bad = prove_scope(storage_ep, bot_ak, bot_sk, bot_bucket, prefix, rc)
        if not bad:
            break
        time.sleep(2)
    if bad:
        say("STOP: the scoped userdata account is not limited as required: " + "; ".join(bad))
        return 1
    say(f"scoped userdata account limited to s3://{bot_bucket}/{prefix}/ (own prefix allowed; recordings and the rest denied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
