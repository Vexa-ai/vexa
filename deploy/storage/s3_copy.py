#!/usr/bin/env python3
"""Verifying, resumable S3 -> S3 copy: moves recordings from an old MinIO into the new storage.

Works between any two S3 endpoints. Each source object is streamed to the target and read back;
it counts as copied only when the target's bytes hash (SHA-256) to the source's. Every verified
object is appended to a manifest in STATE_DIR, so an interrupted run resumes where it stopped
instead of starting over. Nothing is deleted on either side, ever.

Configuration comes from the environment, so no secret appears in a process list:
  SRC_ENDPOINT SRC_ACCESS_KEY SRC_SECRET_KEY SRC_BUCKET    the old store (read only)
  DST_ENDPOINT DST_ACCESS_KEY DST_SECRET_KEY DST_BUCKET    the new store
  STATE_DIR                                                manifest.jsonl, summary.json, COMPLETE

Options: --dry-run (list and compare, copy nothing) - --reverify (re-hash objects a previous run
already verified) - --prefix P (only keys under P).

Exit codes: 0 every source object is in the target with the same size and SHA-256 - 1 some object
failed or differs (listed) - 2 configuration error - 3 source unreachable - 4 target unreachable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

CHUNK = 1 << 20


def say(msg: str) -> None:
    print(f"[copy] {msg}", flush=True)


def env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        say(f"missing {name}")
        sys.exit(2)
    return v


def client(prefix: str):
    return boto3.client(
        "s3", endpoint_url=env(f"{prefix}_ENDPOINT"),
        aws_access_key_id=env(f"{prefix}_ACCESS_KEY"), aws_secret_access_key=env(f"{prefix}_SECRET_KEY"),
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=10, read_timeout=120),
    )


def code_of(e: Exception) -> str:
    if isinstance(e, ClientError):
        return e.response.get("Error", {}).get("Code", "") or str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return f"{type(e).__name__}: {e}"


def listing(c, bucket: str, prefix: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = c.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            out[o["Key"]] = {"size": o["Size"], "etag": o.get("ETag", "").strip('"')}
        if not r.get("IsTruncated"):
            return out
        token = r["NextContinuationToken"]


def sha_of(c, bucket: str, key: str, sink=None) -> tuple[str, int]:
    body = c.get_object(Bucket=bucket, Key=key)["Body"]
    h, n = hashlib.sha256(), 0
    for chunk in iter(lambda: body.read(CHUNK), b""):
        h.update(chunk)
        n += len(chunk)
        if sink is not None:
            sink.write(chunk)
    return h.hexdigest(), n


def wait_reachable(c, bucket: str, what: str, seconds: int) -> str | None:
    deadline, last = time.time() + seconds, ""
    while time.time() < deadline:
        try:
            c.head_bucket(Bucket=bucket)
            return None
        except (ClientError, BotoCoreError) as e:
            last = code_of(e)
            if last in ("404", "NoSuchBucket", "403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
                return last
            time.sleep(2)
    return last or f"{what} did not answer"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reverify", action="store_true")
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()

    state = env("STATE_DIR")
    os.makedirs(state, exist_ok=True)
    manifest_path = os.path.join(state, "manifest.jsonl")
    complete_path = os.path.join(state, "COMPLETE")
    src, dst = client("SRC"), client("DST")
    sb, db = env("SRC_BUCKET"), env("DST_BUCKET")

    err = wait_reachable(src, sb, "source", 90)
    if err:
        say(f"STOP: cannot read bucket '{sb}' at {os.environ['SRC_ENDPOINT']} ({err}). Nothing was copied.")
        return 3
    err = wait_reachable(dst, db, "target", 60)
    if err in ("404", "NoSuchBucket"):
        dst.create_bucket(Bucket=db)
        err = None
    if err:
        say(f"STOP: cannot write bucket '{db}' at {os.environ['DST_ENDPOINT']} ({err}). Nothing was copied.")
        return 4

    verified: dict[str, dict] = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    verified[rec["key"]] = rec

    t0 = time.time()
    source = listing(src, sb, args.prefix)
    target = listing(dst, db, args.prefix)
    total_bytes = sum(o["size"] for o in source.values())
    say(f"source s3://{sb}/{args.prefix} : {len(source)} objects, {total_bytes} bytes")
    say(f"target s3://{db}/{args.prefix} : {len(target)} objects before this run; manifest holds {len(verified)} verified")

    todo, skipped = [], 0
    for key in sorted(source):
        s, v, t = source[key], verified.get(key), target.get(key)
        if v and not args.reverify and v["size"] == s["size"] and v.get("src_etag") == s["etag"] and t and t["size"] == s["size"]:
            skipped += 1
        else:
            todo.append(key)
    say(f"to copy and verify: {len(todo)} · already verified by an earlier run: {skipped}")
    if args.dry_run:
        for key in todo[:50]:
            say(f"  would copy {key} ({source[key]['size']} bytes)")
        return 0

    if os.path.exists(complete_path):
        os.remove(complete_path)  # re-earned below only if this run verifies everything again
    failed: list[dict] = []
    copied_bytes = 0
    with open(manifest_path, "a") as manifest:
        for i, key in enumerate(todo, 1):
            s = source[key]
            try:
                head = src.head_object(Bucket=sb, Key=key)
                extra = {"ContentType": head.get("ContentType") or "application/octet-stream"}
                if head.get("Metadata"):
                    extra["Metadata"] = head["Metadata"]
                with tempfile.TemporaryFile() as spool:
                    src_sha, n = sha_of(src, sb, key, sink=spool)
                    if n != s["size"]:
                        raise RuntimeError(f"source read {n} bytes, listing says {s['size']}")
                    spool.seek(0)
                    dst.upload_fileobj(spool, db, key, ExtraArgs=extra)
                dst_sha, m = sha_of(dst, db, key)
                if (dst_sha, m) != (src_sha, n):
                    raise RuntimeError(f"read-back differs: source {n} bytes sha256 {src_sha[:16]}…, target {m} bytes sha256 {dst_sha[:16]}…")
            except (ClientError, BotoCoreError, RuntimeError, OSError) as e:
                why = code_of(e)
                if why in ("ObjectParentIsFile", "ExistingObjectIsDirectory", "XAdminObjectPathConflict"):
                    why += " (the target stores objects as files, so one key cannot also be a folder of other keys)"
                failed.append({"key": key, "error": why})
                say(f"  FAILED {key}: {why}")
                continue
            rec = {"key": key, "size": n, "sha256": src_sha, "src_etag": s["etag"], "verified_at": int(time.time())}
            manifest.write(json.dumps(rec) + "\n")
            manifest.flush()
            verified[key] = rec
            copied_bytes += n
            if i % 100 == 0 or i == len(todo):
                say(f"  {i}/{len(todo)} copied and verified ({copied_bytes} bytes)")

    # Final pass: every source key must be in the target, same size, and hash-verified (this run or before).
    target = listing(dst, db, args.prefix)
    missing = [k for k in source if k not in target]
    wrong_size = [k for k in source if k in target and target[k]["size"] != source[k]["size"]]
    unverified = [k for k in source if k not in verified or verified[k]["size"] != source[k]["size"]]
    ok = not (failed or missing or wrong_size or unverified)
    summary = {
        "result": "verified" if ok else "incomplete",
        "source": {"endpoint": os.environ["SRC_ENDPOINT"], "bucket": sb, "prefix": args.prefix,
                   "objects": len(source), "bytes": total_bytes},
        "target": {"endpoint": os.environ["DST_ENDPOINT"], "bucket": db, "objects": len(target),
                   "bytes": sum(o["size"] for o in target.values())},
        "copied_this_run": len(todo) - len(failed), "copied_bytes_this_run": copied_bytes,
        "already_verified": skipped, "failed": failed[:100], "missing_in_target": missing[:100],
        "size_mismatch": wrong_size[:100], "not_hash_verified": unverified[:100],
        "check": "every source key present in the target with equal size and equal SHA-256",
        "seconds": round(time.time() - t0, 1), "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(state, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if ok and not args.prefix:
        with open(complete_path, "w") as f:
            json.dump(summary, f, indent=2)
    print("COPY-SUMMARY " + json.dumps({k: summary[k] for k in ("result", "source", "target", "copied_this_run",
                                                                 "already_verified", "seconds")}), flush=True)
    if not ok:
        say(f"INCOMPLETE: {len(failed)} failed, {len(missing)} missing, {len(wrong_size)} size mismatch, "
            f"{len(unverified)} not hash-verified. Details: {os.path.join(state, 'summary.json')}. Re-run to retry.")
        return 1
    say(f"VERIFIED: all {len(source)} source objects ({total_bytes} bytes) are in the target with equal SHA-256.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
