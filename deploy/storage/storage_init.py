#!/usr/bin/env python3
"""Prepare and verify the S3 store meeting-api writes to during Compose bring-up.

Runs inside the meeting-api image, which already carries boto3. Safe to re-run.

1. Resolve the endpoint meeting-api will use, the way meeting-api resolves it: S3_ENDPOINT, else
   MINIO_ENDPOINT (host:port) with MINIO_SECURE.
2. Ensure the bucket exists, then PUT, GET (compare bytes), and DELETE a unique probe under
   .vexa-storage-init/. Every endpoint must pass, including operator-managed S3 stores.
3. Authenticated bots configured against this storage (BOT_S3_ENDPOINT on the storage host, with
   BOT_S3_ACCESS_KEY / BOT_S3_SECRET_KEY / BOT_USERDATA_S3_PATH) -> create or update that scoped
   account, attach a bucket policy that makes it READ-ONLY on its own prefix, and prove the limit:
   its own prefix is listable and readable, writing or deleting there is denied, everything else in
   the bucket is denied. Every bot carries this pair, so no bot can change the stored session; a
   bot's rotated session reaches the store only through meeting-api (which writes with this
   storage's own pair and accepts only the session profile's files).
4. The session store (ADR-0048), when VEXA_SESSIONS_S3_ACCESS_KEY / _SECRET_KEY are set -> its own
   bucket (VEXA_SESSIONS_S3_BUCKET, default vexa-sessions; never the recordings bucket) and, on this
   storage, its own account with a bucket policy on that bucket alone; then prove it: read, write and
   delete in its bucket allowed, every read, list, write and delete in the recordings bucket denied.
   On an operator's own S3 the operator makes the bucket and key; only object I/O is proved.
5. People's photos (admin-api), when VEXA_PHOTOS_S3_ACCESS_KEY / _SECRET_KEY are set -> on this
   storage, admin-api's own account and a bucket-policy grant on the recordings bucket's
   ``person-photos/`` prefix alone (list, read, write, delete), then prove it: all four allowed in the
   prefix, and every read, list, write and delete elsewhere in the bucket (recordings/ included), the
   session store's bucket and the bot userdata prefix denied. Photos already stored there stay where
   they are. On an operator's own S3 the operator makes and scopes the key; I/O under the prefix is
   proved. versitygw holds ONE policy per bucket, so when the bots' userdata prefix lives in the same
   bucket both grants go into that one policy.

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


#: What the bots' account may do, and nothing more: list and read its own prefix.
BOT_USERDATA_ACTIONS = ("s3:ListBucket", "s3:GetObject")


def userdata_policy(bucket: str, prefix: str, principal: str) -> dict:
    """The bots' account: READ-ONLY on ``<prefix>/``. No PutObject, no DeleteObject — a bot restores
    the stored session and never writes it (meeting-api does, with this storage's own pair)."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "VexaBotUserdataList", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"],
             "Condition": {"StringLike": {"s3:prefix": [f"{prefix}/*"]}}},
            {"Sid": "VexaBotUserdataRead", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:GetObject"],
             "Resource": [f"arn:aws:s3:::{bucket}/{prefix}/*"]},
        ],
    }


def prove_scope(endpoint: str, ak: str, sk: str, bucket: str, prefix: str, root) -> list[str]:
    """Own prefix: list and read allowed, write and delete denied. A probe outside it and listings of
    recordings/ and the bucket: denied. The probes are written by root and removed by root."""
    c = client(endpoint, ak, sk)
    nonce = uuid4().hex
    other = f".vexa-storage-init/{nonce}/scope"
    root.put_object(Bucket=bucket, Key=other, Body=b"not yours")
    probe = f"{prefix}/.vexa-scope-check-{nonce}"
    root.put_object(Bucket=bucket, Key=probe, Body=b"stored session")
    new_key = f"{prefix}/.vexa-scope-check-{nonce}-new"
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

    allowed("get own prefix", lambda: c.get_object(Bucket=bucket, Key=probe)["Body"].read())
    allowed("list own prefix", lambda: c.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/"))
    denied("overwrite an object in own prefix", lambda: c.put_object(Bucket=bucket, Key=probe, Body=b"x"))
    denied("create an object in own prefix", lambda: c.put_object(Bucket=bucket, Key=new_key, Body=b"x"))
    denied("delete an object in own prefix", lambda: c.delete_object(Bucket=bucket, Key=probe))
    denied("list recordings/", lambda: c.list_objects_v2(Bucket=bucket, Prefix="recordings/"))
    denied("list the whole bucket", lambda: c.list_objects_v2(Bucket=bucket))
    denied("get an object outside own prefix", lambda: c.get_object(Bucket=bucket, Key=other)["Body"].read())
    denied("overwrite an object outside own prefix", lambda: c.put_object(Bucket=bucket, Key=other, Body=b"x"))
    denied("delete an object outside own prefix", lambda: c.delete_object(Bucket=bucket, Key=other))
    for key, body, where in ((other, b"not yours", "outside"), (probe, b"stored session", "in")):
        try:
            if root.get_object(Bucket=bucket, Key=key)["Body"].read() != body:
                bad.append(f"an object {where} own prefix changed during the check")
        except ClientError:
            bad.append(f"an object {where} own prefix was removed during the check")
    for key in (other, probe, new_key):
        try:
            root.delete_object(Bucket=bucket, Key=key)
        except ClientError:
            pass
    return bad


#: What the session store's account may do — in its own bucket, and nowhere else.
SESSIONS_ACTIONS = ("s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject")


def sessions_policy(bucket: str, principal: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "VexaSessionsList", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"]},
            {"Sid": "VexaSessionsObjects", "Effect": "Allow", "Principal": {"AWS": [principal]},
             "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": [f"arn:aws:s3:::{bucket}/*"]},
        ],
    }


def prove_sessions_scope(endpoint: str, ak: str, sk: str, bucket: str, recordings: str, root) -> list[str]:
    """The sessions key: object I/O in its bucket allowed; the recordings bucket entirely denied.
    A probe in the recordings bucket is written and removed by root."""
    c = client(endpoint, ak, sk)
    nonce = uuid4().hex
    own = f".vexa-storage-init/{nonce}/session"
    other = f".vexa-storage-init/{nonce}/not-sessions"
    root.put_object(Bucket=recordings, Key=other, Body=b"recording")
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

    allowed("write in the sessions bucket", lambda: c.put_object(Bucket=bucket, Key=own, Body=b"v1"))
    allowed("read in the sessions bucket", lambda: c.get_object(Bucket=bucket, Key=own)["Body"].read())
    allowed("delete in the sessions bucket", lambda: c.delete_object(Bucket=bucket, Key=own))
    denied("list the recordings bucket", lambda: c.list_objects_v2(Bucket=recordings))
    denied("read in the recordings bucket", lambda: c.get_object(Bucket=recordings, Key=other)["Body"].read())
    denied("write in the recordings bucket", lambda: c.put_object(Bucket=recordings, Key=other, Body=b"x"))
    denied("delete in the recordings bucket", lambda: c.delete_object(Bucket=recordings, Key=other))
    try:
        if root.get_object(Bucket=recordings, Key=other)["Body"].read() != b"recording":
            bad.append("an object in the recordings bucket changed during the check")
    except ClientError:
        bad.append("an object in the recordings bucket was removed during the check")
    for b, k in ((recordings, other), (bucket, own)):
        try:
            root.delete_object(Bucket=b, Key=k)
        except ClientError:
            pass
    return bad


def prepare_sessions(storage_ep: str, recordings: str) -> int:
    """Step 4 (ADR-0048). 0 ready or not configured, 1 stop."""
    ak = os.getenv("VEXA_SESSIONS_S3_ACCESS_KEY", "").strip()
    sk = os.getenv("VEXA_SESSIONS_S3_SECRET_KEY", "").strip()
    if not (ak and sk):
        say("session store: no VEXA_SESSIONS_S3_ACCESS_KEY / _SECRET_KEY; agent-api keeps chats in the workspace layout")
        return 0
    bucket = os.getenv("VEXA_SESSIONS_S3_BUCKET", "").strip() or "vexa-sessions"
    if bucket == recordings:
        say(f"STOP: VEXA_SESSIONS_S3_BUCKET is the recordings bucket '{recordings}'. The session store needs its own bucket.")
        return 1
    root_ak, root_sk = os.getenv("MINIO_ACCESS_KEY", ""), os.getenv("MINIO_SECRET_KEY", "")
    root_access = {v for v in (root_ak, os.getenv("S3_ACCESS_KEY", "")) if v}
    root_secret = {v for v in (root_sk, os.getenv("S3_SECRET_KEY", "")) if v}
    if ak in root_access:
        say("STOP: VEXA_SESSIONS_S3_ACCESS_KEY is a storage root key. The session store needs its own scoped key pair.")
        return 1
    if sk in root_secret:
        say("STOP: VEXA_SESSIONS_S3_SECRET_KEY is a storage root secret. The session store needs its own scoped key pair.")
        return 1
    ep = os.getenv("VEXA_SESSIONS_S3_ENDPOINT", "").strip() or endpoint_meeting_api_uses()
    if "://" not in ep:
        ep = ("https://" if os.getenv("MINIO_SECURE", "false").lower() == "true" else "http://") + ep
    if hostport(ep)[0] != hostport(storage_ep)[0] or os.getenv("VEXA_SESSIONS_S3_OPERATOR_KEY", "") == "true":
        # An operator's own S3 (or a key the operator made): the bucket and its scope are theirs.
        return 0 if check_readiness(ep, ak, sk, bucket) else 1
    rc = client(storage_ep, root_ak, root_sk)
    if not wait_ready(rc, f"storage at {storage_ep}"):
        return 1
    say(f"session store bucket '{bucket}' {ensure_bucket(rc, bucket)}")
    say(f"session store account {ensure_scoped_account(storage_ep, root_ak, root_sk, ak, sk)}")
    rc.put_bucket_policy(Bucket=bucket, Policy=json.dumps(sessions_policy(bucket, ak)))
    bad = []
    for _ in range(10):  # the gateway caches IAM for a short while
        bad = prove_sessions_scope(storage_ep, ak, sk, bucket, recordings, rc)
        if not bad:
            break
        time.sleep(2)
    if bad:
        say("STOP: the session store's account is not limited as required: " + "; ".join(bad))
        return 1
    say(f"session store account scoped to s3://{bucket}/ (recordings bucket '{recordings}' denied)")
    return 0


#: Where admin-api keeps people's photos (admin_api/app/photo_storage.py PREFIX), and all it may do there.
PHOTOS_PREFIX = "person-photos"
PHOTOS_ACTIONS = ("s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject")


def photos_statements(bucket: str, principal: str) -> list[dict]:
    """admin-api's account: list, read, write and delete under ``person-photos/``, and nothing else."""
    return [
        {"Sid": "VexaPhotosList", "Effect": "Allow", "Principal": {"AWS": [principal]},
         "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"],
         "Condition": {"StringLike": {"s3:prefix": [f"{PHOTOS_PREFIX}/*"]}}},
        {"Sid": "VexaPhotosObjects", "Effect": "Allow", "Principal": {"AWS": [principal]},
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": [f"arn:aws:s3:::{bucket}/{PHOTOS_PREFIX}/*"]},
    ]


def bucket_policy(statements: list[dict]) -> dict:
    return {"Version": "2012-10-17", "Statement": statements}


def prove_photos_scope(endpoint: str, ak: str, sk: str, bucket: str, root, others: "list[tuple[str, str]]") -> list[str]:
    """The photos key: write, read, list and delete (one and many) under person-photos/ allowed. A probe
    under recordings/, one at the bucket's root and one in each of ``others`` (bucket, key prefix):
    read, overwrite and delete denied, and listings of recordings/ and the bucket denied. The foreign
    probes are written and removed by root."""
    c = client(endpoint, ak, sk)
    nonce = uuid4().hex
    own = f"{PHOTOS_PREFIX}/.vexa-storage-init-{nonce}"
    foreign = [(bucket, f"recordings/.vexa-storage-init-{nonce}"), (bucket, f".vexa-storage-init/{nonce}/not-photos")]
    foreign += [(b, f"{p.strip('/') + '/' if p.strip('/') else ''}.vexa-storage-init-{nonce}") for b, p in others]
    for b, k in foreign:
        root.put_object(Bucket=b, Key=k, Body=b"not a photo")
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

    allowed("write a photo", lambda: c.put_object(Bucket=bucket, Key=own, Body=b"photo"))
    allowed("read a photo", lambda: c.get_object(Bucket=bucket, Key=own)["Body"].read())
    allowed("list person-photos/", lambda: c.list_objects_v2(Bucket=bucket, Prefix=f"{PHOTOS_PREFIX}/"))
    allowed("delete a photo", lambda: c.delete_object(Bucket=bucket, Key=own))
    allowed("write a second photo", lambda: c.put_object(Bucket=bucket, Key=own + "-2", Body=b"photo"))

    def delete_many():
        r = c.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": own + "-2"}]})
        if r.get("Errors"):
            raise ClientError({"Error": {"Code": r["Errors"][0].get("Code", "AccessDenied")}}, "DeleteObjects")
    allowed("delete a person's photos in one call", delete_many)
    denied("list recordings/", lambda: c.list_objects_v2(Bucket=bucket, Prefix="recordings/"))
    denied("list the whole bucket", lambda: c.list_objects_v2(Bucket=bucket))
    for b, k in foreign:
        where = f"s3://{b}/{k.rsplit('/', 1)[0] + '/' if '/' in k else ''}"
        denied(f"read in {where}", lambda b=b, k=k: c.get_object(Bucket=b, Key=k)["Body"].read())
        denied(f"overwrite in {where}", lambda b=b, k=k: c.put_object(Bucket=b, Key=k, Body=b"x"))
        denied(f"delete in {where}", lambda b=b, k=k: c.delete_object(Bucket=b, Key=k))
    for b, k in foreign:
        try:
            if root.get_object(Bucket=b, Key=k)["Body"].read() != b"not a photo":
                bad.append(f"s3://{b}/{k} changed during the check")
        except ClientError:
            bad.append(f"s3://{b}/{k} was removed during the check")
    for b, k in foreign + [(bucket, own), (bucket, own + "-2")]:
        try:
            root.delete_object(Bucket=b, Key=k)
        except ClientError:
            pass
    return bad


def check_photos_io(endpoint: str, ak: str, sk: str, bucket: str) -> bool:
    """An operator's own S3: the photos key writes, reads and deletes a probe under person-photos/."""
    key = f"{PHOTOS_PREFIX}/.vexa-storage-init-{uuid4().hex}"
    try:
        c = client(endpoint, ak, sk)
        c.put_object(Bucket=bucket, Key=key, Body=b"photo")
        if c.get_object(Bucket=bucket, Key=key)["Body"].read() != b"photo":
            raise ValueError("probe bytes differ")
        c.delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError, ValueError, OSError) as e:
        say(f"STOP: the photos key cannot write, read and delete under s3://{bucket}/{PHOTOS_PREFIX}/ at {endpoint} ({code_of(e)})")
        return False
    say(f"photos key: PUT/GET/DELETE verified under s3://{bucket}/{PHOTOS_PREFIX}/ at {endpoint}")
    return True


def photos_key() -> "tuple[str, str] | None | int":
    """admin-api's photos pair, None when unset, 1 when it is a root pair's (a stop)."""
    ak = os.getenv("VEXA_PHOTOS_S3_ACCESS_KEY", "").strip()
    sk = os.getenv("VEXA_PHOTOS_S3_SECRET_KEY", "").strip()
    if not (ak and sk):
        say("people's photos: no VEXA_PHOTOS_S3_ACCESS_KEY / _SECRET_KEY; admin-api shows initials")
        return None
    root_access = {v for v in (os.getenv("MINIO_ACCESS_KEY", ""), os.getenv("S3_ACCESS_KEY", "")) if v}
    root_secret = {v for v in (os.getenv("MINIO_SECRET_KEY", ""), os.getenv("S3_SECRET_KEY", "")) if v}
    if ak in root_access:
        say("STOP: VEXA_PHOTOS_S3_ACCESS_KEY is a storage root or recordings key. admin-api's photos need their own scoped key pair.")
        return 1
    if sk in root_secret:
        say("STOP: VEXA_PHOTOS_S3_SECRET_KEY is a storage root or recordings secret. admin-api's photos need their own scoped key pair.")
        return 1
    return ak, sk


def main(argv: "list[str] | None" = None) -> int:
    storage_ep = os.getenv("STORAGE_ENDPOINT", "http://storage:9000").strip()
    if "--sessions-only" in (argv or []):
        # Vexa Lite: step 4 alone, against its bundled storage sidecar (ADR-0048).
        bucket = os.getenv("MINIO_BUCKET", os.getenv("RECORDING_BUCKET", "vexa"))
        return prepare_sessions(storage_ep, bucket)
    ep = endpoint_meeting_api_uses()
    access = os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY", "")
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY", "")
    bucket = os.getenv("MINIO_BUCKET", os.getenv("RECORDING_BUCKET", "vexa"))
    if not check_readiness(ep, access, secret, bucket):
        return 1
    if hostport(ep) != hostport(storage_ep):
        say(f"WARNING: recordings go to {ep} (bucket '{bucket}'), not the bundled storage at {storage_ep}. "
            f"Expected if that is your own S3; if this install was upgraded from MinIO, see {UPGRADE_DOC}")
    if prepare_sessions(storage_ep, bucket) != 0:
        return 1
    photos = photos_key()
    if photos == 1:
        return 1
    if photos and hostport(ep)[0] != hostport(storage_ep)[0]:
        # Recordings (and so photos) live on an operator's own S3: the key and its scope are theirs.
        if not check_photos_io(ep, photos[0], photos[1], bucket):
            return 1
        photos = None

    bot_ep = os.getenv("BOT_S3_ENDPOINT", "").strip()
    bot_ak, bot_sk = os.getenv("BOT_S3_ACCESS_KEY", "").strip(), os.getenv("BOT_S3_SECRET_KEY", "").strip()
    bots = bool(bot_ep and bot_ak and bot_sk) and hostport(bot_ep)[0] == hostport(storage_ep)[0]
    if not (bots or photos):
        return 0
    prefix = os.getenv("BOT_USERDATA_S3_PATH", "").strip().strip("/")
    bot_bucket = os.getenv("BOT_S3_BUCKET", "").strip() or bucket
    root_ak, root_sk = os.getenv("MINIO_ACCESS_KEY", ""), os.getenv("MINIO_SECRET_KEY", "")
    if bots:
        if not prefix:
            say("STOP: BOT_S3_* point at this storage but BOT_USERDATA_S3_PATH is empty; the scoped account needs its own prefix.")
            return 1
        if prefix == PHOTOS_PREFIX or prefix.startswith(PHOTOS_PREFIX + "/"):
            say(f"STOP: BOT_USERDATA_S3_PATH is under {PHOTOS_PREFIX}/, admin-api's photos prefix. Bot userdata needs its own prefix.")
            return 1
        # Neither half of the bots' pair may be a root pair's (this storage's or an operator S3's):
        # meeting-api refuses such a spawn too. Names only — never a value.
        root_access = {v for v in (root_ak, os.getenv("S3_ACCESS_KEY", "")) if v}
        root_secret = {v for v in (root_sk, os.getenv("S3_SECRET_KEY", "")) if v}
        if bot_ak in root_access:
            say("STOP: BOT_S3_ACCESS_KEY is the storage root key. Authenticated bots need their own scoped key pair.")
            return 1
        if bot_sk in root_secret:
            say("STOP: BOT_S3_SECRET_KEY is a storage root secret. Authenticated bots need their own scoped key pair.")
            return 1
    rc = client(storage_ep, root_ak, root_sk)
    if not wait_ready(rc, f"storage at {storage_ep}"):
        return 1
    # versitygw holds one policy per bucket: every grant on a bucket goes into that bucket's one policy.
    grants: dict[str, list[dict]] = {}
    if photos:
        say(f"people's photos account {ensure_scoped_account(storage_ep, root_ak, root_sk, photos[0], photos[1])}")
        grants.setdefault(bucket, []).extend(photos_statements(bucket, photos[0]))
    if bots:
        say(f"bucket '{bot_bucket}' {ensure_bucket(rc, bot_bucket)} for bot userdata")
        say(f"scoped userdata account {ensure_scoped_account(storage_ep, root_ak, root_sk, bot_ak, bot_sk)}")
        grants.setdefault(bot_bucket, []).extend(userdata_policy(bot_bucket, prefix, bot_ak)["Statement"])
    for b, statements in grants.items():
        rc.put_bucket_policy(Bucket=b, Policy=json.dumps(bucket_policy(statements)))
    sessions_bucket = os.getenv("VEXA_SESSIONS_S3_BUCKET", "").strip() or "vexa-sessions"
    others = [(bot_bucket, prefix)] if bots else []
    if os.getenv("VEXA_SESSIONS_S3_ACCESS_KEY", "").strip():
        others.append((sessions_bucket, ""))
    bad = []
    for _ in range(10):  # the gateway caches IAM for a short while; give a fresh account a moment
        bad = []
        if bots:
            bad += prove_scope(storage_ep, bot_ak, bot_sk, bot_bucket, prefix, rc)
        if photos:
            bad += prove_photos_scope(storage_ep, photos[0], photos[1], bucket, rc, others)
        if not bad:
            break
        time.sleep(2)
    if bad:
        say("STOP: a scoped account is not limited as required: " + "; ".join(bad))
        return 1
    if bots:
        say(f"scoped userdata account read-only on s3://{bot_bucket}/{prefix}/ (list and read allowed; writes, recordings and the rest denied)")
    if photos:
        say(f"people's photos account scoped to s3://{bucket}/{PHOTOS_PREFIX}/ (list, read, write, delete; recordings, the rest of the bucket and other buckets denied)")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
