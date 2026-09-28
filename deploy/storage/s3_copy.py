#!/usr/bin/env python3
"""Verifying, resumable S3 -> S3 copy: moves recordings from an old MinIO into the new storage.

Works between any two S3 endpoints. Uploaded objects are read back and compared by SHA-256.
The manifest in STATE_DIR records the verified source and target state. A re-run copies only
objects new or changed in the source, and never overwrites or re-creates objects the target
changed or deleted since verification. Untracked target objects are adopted if their bytes
match the source; otherwise they are left alone. Changes and deletions are reported in
summary.json. Nothing is deleted on either side, ever.

Configuration comes from the environment, so no secret appears in a process list:
  SRC_ENDPOINT SRC_ACCESS_KEY SRC_SECRET_KEY SRC_BUCKET    the old store (read only)
  DST_ENDPOINT DST_ACCESS_KEY DST_SECRET_KEY DST_BUCKET    the new store
  STATE_DIR                                                manifest.jsonl, summary.json

Options: --dry-run (list and compare, copy nothing) - --reverify (re-hash verified target objects
whose size and ETag still match the manifest) - --prefix P (only keys under P).

Exit codes: 0 every source object is verified or reported as changed/deleted (also dry-run) -
1 some object failed or differs during verification (listed) - 2 configuration error -
3 source unreachable - 4 target unreachable.
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


def source_matches(record: dict | None, obj: dict) -> bool:
    return record is not None and (record["size"], record.get("src_etag")) == (obj["size"], obj["etag"])


def target_matches(record: dict, obj: dict | None) -> bool:
    return (obj is not None and record.get("dst_etag") is not None
            and (record.get("dst_size"), record["dst_etag"]) == (obj["size"], obj["etag"]))


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

    reports = {name: [] for name in (
        "deleted_in_target_after_copy", "changed_in_target_after_copy",
        "changed_on_both_sides", "deleted_at_source_after_copy",
    )}
    reports["deleted_at_source_after_copy"] = sorted(
        k for k in verified if k.startswith(args.prefix) and k not in source
    )
    todo, skipped = [], 0
    for key in sorted(source):
        s, v, t = source[key], verified.get(key), target.get(key)
        if source_matches(v, s):
            skipped += 1
            if t is None:
                reports["deleted_in_target_after_copy"].append(key)
            elif not target_matches(v, t):
                if v.get("dst_etag") is not None and v.get("dst_size") == t["size"]:
                    todo.append((key, "refresh-etag"))
                else:
                    reports["changed_in_target_after_copy"].append(key)
            elif args.reverify:
                todo.append((key, "reverify"))
        elif v is not None:
            if target_matches(v, t):
                todo.append((key, "copy"))
            else:
                reports["changed_on_both_sides"].append(key)
        else:
            todo.append((key, "adopt" if t is not None else "copy"))
    say(f"to copy: {sum(action == 'copy' for _, action in todo)} · "
        f"to compare for adoption: {sum(action == 'adopt' for _, action in todo)} · "
        f"to reverify: {sum(action in ('reverify', 'refresh-etag') for _, action in todo)} · already verified: {skipped}")
    if args.dry_run:
        for key, action in todo[:50]:
            label = "compare for adoption" if action == "adopt" else action
            say(f"  would {label} {key} ({source[key]['size']} bytes)")
        for name, keys in reports.items():
            if keys:
                say(f"{name}: {len(keys)}")
        return 0

    failed: list[dict] = []
    copied = copied_bytes = adopted = 0
    with open(manifest_path, "a") as manifest:
        for i, (key, action) in enumerate(todo, 1):
            s = source[key]
            try:
                if action == "refresh-etag":
                    dst_sha, m = sha_of(dst, db, key)
                    if (dst_sha, m) != (verified[key]["sha256"], verified[key]["dst_size"]):
                        reports["changed_in_target_after_copy"].append(key)
                        continue
                    rec = {**verified[key], "dst_etag": target[key]["etag"]}
                    manifest.write(json.dumps(rec) + "\n")
                    manifest.flush()
                    verified[key] = rec
                    continue
                if action == "reverify":
                    dst_sha, m = sha_of(dst, db, key)
                    if (dst_sha, m) != (verified[key]["sha256"], verified[key]["dst_size"]):
                        raise RuntimeError("target SHA-256 or size differs from the verified manifest")
                    continue
                if action == "adopt":
                    dst_sha, m = sha_of(dst, db, key)
                    src_sha, n = sha_of(src, sb, key)
                    if n != s["size"]:
                        raise RuntimeError(f"source read {n} bytes, listing says {s['size']}")
                    if (dst_sha, m) != (src_sha, n):
                        reports["changed_on_both_sides"].append(key)
                        continue
                else:
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
                dst_head = dst.head_object(Bucket=db, Key=key)
                rec = {"key": key, "size": n, "sha256": src_sha, "src_etag": s["etag"],
                       "dst_etag": dst_head["ETag"].strip('"'), "dst_size": dst_head["ContentLength"],
                       "verified_at": int(time.time())}
            except (ClientError, BotoCoreError, RuntimeError, OSError) as e:
                why = code_of(e)
                if why in ("ObjectParentIsFile", "ExistingObjectIsDirectory", "XAdminObjectPathConflict"):
                    why += " (the target stores objects as files, so one key cannot also be a folder of other keys)"
                failed.append({"key": key, "error": why})
                say(f"  FAILED {key}: {why}")
                continue
            manifest.write(json.dumps(rec) + "\n")
            manifest.flush()
            verified[key] = rec
            if action == "adopt":
                adopted += 1
            else:
                copied += 1
                copied_bytes += n
            if i % 100 == 0 or i == len(todo):
                say(f"  {copied} copied and verified ({copied_bytes} bytes), {adopted} adopted")

    # Target differences are observations; completion comes from verification or an explicit report.
    target = listing(dst, db, args.prefix)
    missing = [k for k in source if k not in target]
    wrong_size = [k for k in source if k in target and target[k]["size"] != source[k]["size"]]
    unverified = [k for k in source if not source_matches(verified.get(k), source[k])]
    reported = {key for keys in reports.values() for key in keys}
    ok = not failed and all(key in reported for key in unverified)
    for name, keys in reports.items():
        keys.sort()
        if keys:
            say(f"{name}: {len(keys)}")
    summary = {
        "result": "verified" if ok else "incomplete",
        "source": {"endpoint": os.environ["SRC_ENDPOINT"], "bucket": sb, "prefix": args.prefix,
                   "objects": len(source), "bytes": total_bytes},
        "target": {"endpoint": os.environ["DST_ENDPOINT"], "bucket": db, "objects": len(target),
                   "bytes": sum(o["size"] for o in target.values())},
        "copied_this_run": copied, "copied_bytes_this_run": copied_bytes, "adopted": adopted,
        "already_verified": skipped, "failed": failed[:100], "missing_in_target": missing[:100],
        "size_mismatch": wrong_size[:100], "not_hash_verified": unverified[:100],
        **{name: keys[:1000] for name, keys in reports.items()},
        "check": "every source key verified against its current source state or reported as changed/deleted; no verification failed",
        "seconds": round(time.time() - t0, 1), "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(state, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("COPY-SUMMARY " + json.dumps({k: summary[k] for k in ("result", "source", "target", "copied_this_run",
                                                                 "already_verified", "seconds", "adopted")}), flush=True)
    if not ok:
        say(f"INCOMPLETE: {len(failed)} failed, {len(missing)} missing, {len(wrong_size)} size mismatch, "
            f"{len(unverified)} not hash-verified. Details: {os.path.join(state, 'summary.json')}. Re-run to retry.")
        return 1
    preserved = sum(len(reports[name]) for name in (
        "deleted_in_target_after_copy", "changed_in_target_after_copy", "changed_on_both_sides",
    ))
    say(f"VERIFIED: {len(source) - preserved} source objects verified "
        f"({copied} copied this run, {adopted} adopted); {preserved} changed or deleted in the new storage "
        "after the switch and left as they are.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
