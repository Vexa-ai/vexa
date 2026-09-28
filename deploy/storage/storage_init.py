#!/usr/bin/env python3
"""Prepare and verify the S3 store meeting-api writes to during Compose bring-up.

Runs inside the meeting-api image, which already carries boto3. Safe to re-run.

1. Resolve the endpoint meeting-api will use, the way meeting-api resolves it: S3_ENDPOINT, else
   MINIO_ENDPOINT (host:port) with MINIO_SECURE.
2. Ensure the bucket exists, then PUT, GET (compare bytes), and DELETE a unique probe under
   .vexa-storage-init/. Every endpoint must pass, including operator-managed S3 stores.
3. Authenticated bots configured against this storage (BOT_S3_ENDPOINT on the storage host, with
   BOT_S3_ACCESS_KEY / BOT_S3_SECRET_KEY / BOT_USERDATA_S3_PATH) -> create or update that scoped
   account, attach a bucket policy that limits it to its own prefix, and prove the limit: its own
   prefix is readable and writable, everything else in the bucket is denied.

Exit codes: 0 ready - 1 the stack must not start (message says why).
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
from uuid import uuid4
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
    explicit = os.getenv("S3_ENDPOINT")
    if explicit:
        return explicit
    ep = os.getenv("MINIO_ENDPOINT", "minio:9000")
    if ep.startswith(("http://", "https://")):
        return ep
    scheme = "https" if os.getenv("MINIO_SECURE", "false").lower() == "true" else "http"
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


def check_readiness(endpoint: str, access: str, secret: str, bucket: str) -> bool:
    """Prove bucket access and object I/O without touching any recording keys."""
    step = "connect"
    c = None
    key = f".vexa-storage-init/{uuid4().hex}"
    payload = b"vexa storage readiness"
    probe_written = False
    try:
        c = client(endpoint, access, secret)
        step = "HEAD bucket"
        try:
            c.head_bucket(Bucket=bucket)
        except ClientError as e:
            if code_of(e) not in ("404", "NoSuchBucket", "NotFound"):
                raise
            step = "CREATE bucket"
            try:
                c.create_bucket(Bucket=bucket)
            except ClientError as e:
                if code_of(e) != "BucketAlreadyOwnedByYou":
                    raise
        step = "PUT probe"
        c.put_object(Bucket=bucket, Key=key, Body=payload)
        probe_written = True
        step = "GET probe"
        body = c.get_object(Bucket=bucket, Key=key)["Body"]
        try:
            actual = body.read()
        finally:
            body.close()
        step = "compare bytes"
        if actual != payload:
            raise ValueError("probe bytes differ")
        step = "DELETE probe"
        c.delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError, ValueError, OSError) as e:
        cleanup = ""
        if probe_written and step != "DELETE probe":
            try:
                c.delete_object(Bucket=bucket, Key=key)
            except (BotoCoreError, ClientError, OSError) as cleanup_error:
                cleanup = f"; DELETE probe cleanup also failed ({code_of(cleanup_error)})"
        say(f"STOP: endpoint {endpoint}, bucket '{bucket}', step {step} failed ({code_of(e)}){cleanup}. "
            f"For installs upgraded from MinIO, see {UPGRADE_DOC}")
        return False
    say(f"Ready: endpoint {endpoint}, bucket '{bucket}'; PUT/GET bytes verified and probe deleted.")
    return True


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
    """Own prefix: allowed. A probe outside it and listings of recordings/ and the bucket: denied."""
    c = client(endpoint, ak, sk)
    nonce = uuid4().hex
    other = f".vexa-storage-init/{nonce}/scope"
    root.put_object(Bucket=bucket, Key=other, Body=b"not yours")
    probe = f"{prefix}/.vexa-scope-check-{nonce}"
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
    denied("get an object outside own prefix", lambda: c.get_object(Bucket=bucket, Key=other)["Body"].read())
    denied("overwrite an object outside own prefix", lambda: c.put_object(Bucket=bucket, Key=other, Body=b"x"))
    denied("delete an object outside own prefix", lambda: c.delete_object(Bucket=bucket, Key=other))
    if root.get_object(Bucket=bucket, Key=other)["Body"].read() != b"not yours":
        bad.append("an object outside own prefix changed during the check")
    root.delete_object(Bucket=bucket, Key=other)
    return bad


def main() -> int:
    storage_ep = os.getenv("STORAGE_ENDPOINT", "http://storage:9000").strip()
    ep = endpoint_meeting_api_uses()
    access = os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY", "")
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY", "")
    bucket = os.getenv("MINIO_BUCKET", os.getenv("RECORDING_BUCKET", "vexa"))
    if not check_readiness(ep, access, secret, bucket):
        return 1

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
