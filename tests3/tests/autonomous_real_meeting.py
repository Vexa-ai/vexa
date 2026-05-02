#!/usr/bin/env python3
"""
autonomous_real_meeting.py — comprehensive end-to-end real-meeting test harness.

Covers v0.10.2 → v0.10.6 customer-visible regressions in a single live-bot run:
  - bot-lifecycle (create/admit/recording/stop)
  - Pack M chunk-leak (memory growth bounded)
  - Pack U server-side master finalizer (any-exit playable recording)
  - Pack U meeting_data JSONB path (production default)
  - Pack U unified alignment hook (segments fit within audio)
  - Pack O bot stdout JSONB capture
  - Pack T bot resource telemetry
  - Pack C user-stop classifier
  - Pack D-3 download presigned URL
  - Pack FM-274 hallucination corpus shipped (file presence)
  - #304 dashboard pagination dedupe (separate, unrelated to bot run)
  - Crash-safety (SIGKILL path) — recording still playable

Inputs (CLI flags or env):
  --platform        gmeet | teams | zoom_web
  --url             real meeting URL (delivered on demand by operator)
  --deployment      compose | helm | lite      (resolves gateway+token from .state)
  --mode            normal | crash             (default: normal)
  --duration        seconds to record          (default: 240)
  --gateway-url     override                   (default: read from .state)
  --api-token       override                   (default: read from .state)
  --admin-token     override                   (admin-required ops)
  --bot-name        bot display name           (default: tests3-auto)
  --output          report JSON path           (default: tests3/.state/reports/<mode>/auto-real-<platform>-<ts>.json)

Phases:
  1. resolve endpoint / token
  2. dispatch bot
  3. wait for admission (status=active, signal: ADMIT BOT NOW: <url>)
  4. record-monitor (sample meeting state every 10s for <duration>)
  5. terminate (normal=DELETE; crash=docker/kubectl kill)
  6. wait for callback (meeting.status=completed/failed)
  7. assert (15+ assertions)
  8. emit report

Exit codes:
  0  all assertions pass (or skipped with documented reason)
  1  one or more assertions fail — see report
  2  harness error (bad args, network, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "tests3"

PLATFORM_NATIVE_KEY = {
    "gmeet": "google_meet",
    "teams": "teams",
    "zoom_web": "zoom",  # bot platform string
}

PLATFORM_DISPATCH_KEY = {
    "gmeet": "google_meet",
    "teams": "teams",
    "zoom_web": "zoom",
}


# ─── http helpers ───────────────────────────────────────────────────────

class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"HTTP {status} on {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


def http(
    method: str,
    url: str,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    timeout: int = 30,
) -> dict | list | str:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-API-Key"] = token
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(raw) if raw else {}
            return raw
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", errors="replace"), url) from None
    except urllib.error.URLError as e:
        raise HttpError(0, str(e), url) from None


# ─── deployment endpoint resolution ─────────────────────────────────────

def state_path(deployment: str) -> Path:
    return STATE / f".state-{deployment}"


def read_state(deployment: str, key: str) -> Optional[str]:
    p = state_path(deployment) / key
    if p.is_file():
        return p.read_text().strip() or None
    return None


def resolve_endpoints(args: argparse.Namespace) -> tuple[str, str, Optional[str]]:
    gateway = (
        args.gateway_url
        or os.environ.get("GATEWAY_URL")
        or read_state(args.deployment, "gateway_url")
    )
    # compose: fall back to the unsuffixed .state/gateway_url, then localhost
    if not gateway and args.deployment == "compose":
        unsuffixed = STATE / ".state" / "gateway_url"
        if unsuffixed.is_file():
            gateway = unsuffixed.read_text().strip()
    if not gateway and args.deployment == "compose":
        gateway = "http://localhost:8056"
    # lite: synthesize from vm_ip if gateway_url not stored
    if not gateway and args.deployment == "lite":
        ip = read_state("lite", "vm_ip")
        if ip:
            gateway = f"http://{ip}:8056"

    token = (
        args.api_token
        or os.environ.get("API_TOKEN")
        or read_state(args.deployment, "api_token")
    )
    # compose: fall back to BOT_API_TOKEN in .env at repo root
    if not token and args.deployment == "compose":
        env_path = ROOT / ".env"
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                if line.startswith("BOT_API_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    admin = (
        args.admin_token
        or os.environ.get("ADMIN_TOKEN")
        or read_state(args.deployment, "admin_token")
    )
    if not gateway:
        die(f"could not resolve gateway URL for deployment={args.deployment} "
            "(set --gateway-url or GATEWAY_URL or write .state-<dep>/gateway_url)")
    if not token:
        die(f"could not resolve API_TOKEN for deployment={args.deployment} "
            "(set --api-token or API_TOKEN or write .state-<dep>/api_token)")
    return gateway, token, admin


def die(msg: str, code: int = 2) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─── bot dispatch / lifecycle ───────────────────────────────────────────

def dispatch_bot(gateway: str, token: str, platform: str, url: str, name: str) -> dict:
    native_id, passcode = extract_native_id_and_pass(platform, url)
    payload: dict[str, Any] = {
        "platform": PLATFORM_DISPATCH_KEY[platform],
        "native_meeting_id": native_id,
        "bot_name": name,
        "language": "en",
    }
    if passcode:
        payload["passcode"] = passcode
    log(f"POST /bots → {payload['platform']} / {native_id}{' (+passcode)' if passcode else ''}")
    r = http("POST", f"{gateway}/bots", token=token, body=payload, timeout=30)
    assert isinstance(r, dict)
    return r


def extract_native_id_and_pass(platform: str, url: str) -> tuple[str, Optional[str]]:
    """Extract platform-specific meeting id + passcode from URL.

    GMeet: https://meet.google.com/abc-defg-hij                              → ("abc-defg-hij", None)
    Teams: https://teams.microsoft.com/meet/<id>?p=<passcode>                → ("<id>", "<passcode>")
    Zoom:  https://us04web.zoom.us/j/<id>?pwd=<passcode>                     → ("<id>", "<passcode>")
    """
    p = urllib.parse.urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    qs = urllib.parse.parse_qs(p.query)
    if platform == "gmeet":
        for x in parts:
            if "-" in x and len(x) >= 11:
                return x, None
        return (parts[-1] if parts else url), None
    if platform == "teams":
        nid = url
        if "meet" in parts:
            i = parts.index("meet")
            if i + 1 < len(parts):
                nid = parts[i + 1]
        else:
            nid = parts[-1] if parts else url
        passcode = (qs.get("p") or [None])[0]
        return nid, passcode
    if platform == "zoom_web":
        nid = parts[-1] if parts else url
        if "j" in parts:
            i = parts.index("j")
            if i + 1 < len(parts):
                nid = parts[i + 1]
        passcode = (qs.get("pwd") or [None])[0]
        return nid, passcode
    return url, None


def extract_native_id(platform: str, url: str) -> str:
    """Backwards-compat wrapper: id only, drops passcode."""
    return extract_native_id_and_pass(platform, url)[0]


def get_meeting(gateway: str, token: str, platform: str, native_id: str) -> Optional[dict]:
    plat = PLATFORM_NATIVE_KEY[platform]
    try:
        r = http("GET", f"{gateway}/meetings/{plat}/{native_id}", token=token, timeout=15)
        return r if isinstance(r, dict) else None
    except HttpError as e:
        if e.status == 404:
            return None
        raise


def list_meetings(gateway: str, token: str, limit: int = 5) -> list[dict]:
    r = http("GET", f"{gateway}/meetings?limit={limit}", token=token, timeout=15)
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        for k in ("meetings", "items", "data"):
            v = r.get(k)
            if isinstance(v, list):
                return v
    return []


def stop_bot_normal(gateway: str, token: str, platform: str, native_id: str) -> None:
    plat = PLATFORM_NATIVE_KEY[platform]
    log(f"DELETE /bots/{plat}/{native_id}")
    http("DELETE", f"{gateway}/bots/{plat}/{native_id}", token=token, timeout=30)


def kill_bot_crash(deployment: str, native_id: str) -> bool:
    """SIGKILL the bot container/pod. Returns True on success."""
    if deployment == "compose":
        # docker container name pattern: vexa-bot-<...> — find the one whose env names match the meeting
        try:
            cl = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}", "--filter", "name=vexa-bot-"],
                capture_output=True, text=True, timeout=10,
            )
            for line in cl.stdout.splitlines():
                name = line.split("\t")[0]
                # cheap: docker inspect for env containing the native_id
                ins = subprocess.run(
                    ["docker", "inspect", "--format", "{{json .Config.Env}}", name],
                    capture_output=True, text=True, timeout=10,
                )
                if native_id in ins.stdout:
                    log(f"docker kill -s KILL {name}")
                    subprocess.run(["docker", "kill", "-s", "KILL", name], timeout=10)
                    return True
        except Exception as e:
            log(f"crash-kill compose failed: {e}")
            return False
        return False
    if deployment == "helm":
        # find pod via label selector or env containing native_id
        try:
            pods = subprocess.run(
                ["kubectl", "--kubeconfig", str(state_path("helm") / "lke_kubeconfig"),
                 "get", "pods", "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(pods.stdout)
            for p in data.get("items", []):
                envs = p.get("spec", {}).get("containers", [{}])[0].get("env", [])
                if any(native_id in str(e.get("value", "")) for e in envs):
                    pn = p["metadata"]["name"]
                    log(f"kubectl delete pod {pn} --grace-period=0 --force")
                    subprocess.run(
                        ["kubectl", "--kubeconfig", str(state_path("helm") / "lke_kubeconfig"),
                         "delete", "pod", pn, "--grace-period=0", "--force"],
                        timeout=15,
                    )
                    return True
        except Exception as e:
            log(f"crash-kill helm failed: {e}")
            return False
        return False
    if deployment == "lite":
        # ssh root@<vm_ip> "docker kill -s KILL <name>"
        ip = read_state("lite", "vm_ip")
        if not ip:
            log("crash-kill lite: no vm_ip in state")
            return False
        try:
            cl = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}",
                 f"docker ps --format '{{{{.Names}}}}' --filter name=vexa-bot- | while read n; do "
                 f"docker inspect --format '{{{{json .Config.Env}}}}' $n | grep -q {shlex.quote(native_id)} && "
                 f"docker kill -s KILL $n && break; done"],
                capture_output=True, text=True, timeout=20,
            )
            return cl.returncode == 0
        except Exception as e:
            log(f"crash-kill lite failed: {e}")
            return False
    return False


# ─── monitor phase: sample state every 10s ──────────────────────────────

@dataclass
class Sample:
    t: float
    status: Optional[str] = None
    bot_status: Optional[str] = None
    chunk_count: Optional[int] = None
    rss_bytes: Optional[int] = None


def sample_meeting(gateway: str, token: str, platform: str, native_id: str) -> Sample:
    s = Sample(t=time.time())
    m = get_meeting(gateway, token, platform, native_id)
    if m:
        s.status = m.get("status") or m.get("meeting_status")
        data = m.get("data") or {}
        recs = data.get("recordings") or []
        if recs:
            mfs = recs[0].get("media_files") or []
            audio = next((mf for mf in mfs if mf.get("type") == "audio"), mfs[0] if mfs else None)
            if audio:
                s.chunk_count = audio.get("chunk_count")
    return s


# ─── ASSERTIONS ─────────────────────────────────────────────────────────

@dataclass
class AssertionResult:
    id: str
    status: str  # pass | fail | skip
    message: str = ""
    expected: Any = None
    actual: Any = None


@dataclass
class Report:
    started_at: str
    deployment: str
    platform: str
    mode: str
    duration_sec: int
    meeting_url: str
    native_id: Optional[str] = None
    meeting_internal_id: Optional[int] = None
    bot_name: str = ""
    final_status: Optional[str] = None
    storage_path: Optional[str] = None
    finalize_marker: Optional[str] = None
    master_size_bytes: Optional[int] = None
    master_duration_sec: Optional[float] = None
    samples: list[dict] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    verdict: str = "running"

    def add(self, r: AssertionResult) -> None:
        self.assertions.append(asdict(r))


def passed(report: Report, aid: str, msg: str = "", actual: Any = None) -> None:
    report.add(AssertionResult(aid, "pass", msg, actual=actual))
    log(f"  ✓ {aid}{(' — ' + msg) if msg else ''}")


def failed(report: Report, aid: str, msg: str, expected: Any = None, actual: Any = None) -> None:
    report.add(AssertionResult(aid, "fail", msg, expected=expected, actual=actual))
    log(f"  ✗ {aid} — {msg}")


def skipped(report: Report, aid: str, reason: str) -> None:
    report.add(AssertionResult(aid, "skip", reason))
    log(f"  ⊘ {aid} — {reason}")


# ─── verifier ───────────────────────────────────────────────────────────

def verify_recording(
    report: Report, gateway: str, token: str, meeting: dict
) -> None:
    """All meeting-level assertions after callback fires."""
    data = meeting.get("data") or {}
    recs = data.get("recordings") or []

    # bot-lifecycle: status-completed (Pack C: user-stop should be completed)
    final = meeting.get("status") or meeting.get("meeting_status")
    report.final_status = final
    if report.mode == "normal":
        if final == "completed":
            passed(report, "STATUS_COMPLETED_ON_NORMAL_STOP", actual=final)
        else:
            failed(report, "STATUS_COMPLETED_ON_NORMAL_STOP",
                   f"expected completed, got {final}", expected="completed", actual=final)
    else:  # crash
        if final in ("completed", "failed"):
            passed(report, "STATUS_TERMINAL_ON_CRASH", actual=final)
        else:
            failed(report, "STATUS_TERMINAL_ON_CRASH",
                   f"expected completed/failed, got {final}", actual=final)

    # post-meeting-transcription: server-side finalizer ran
    if not recs:
        failed(report, "MEETING_HAS_RECORDING", "no recordings in meeting.data — finalizer never ran",
               expected=">=1 recording", actual=0)
        return
    passed(report, "MEETING_HAS_RECORDING", f"{len(recs)} recording(s)")

    rec = recs[0]
    media_files = rec.get("media_files") or []
    audio_mf = next((mf for mf in media_files if mf.get("type") == "audio"), media_files[0] if media_files else {})

    # finalize marker lives on media_file.finalized_by (per actual API shape — checked via /meetings)
    finalize = audio_mf.get("finalized_by") or rec.get("finalize")
    report.finalize_marker = finalize
    if finalize == "recording_finalizer.master":
        passed(report, "FINALIZE_MARKER_IS_SERVER_SIDE_MASTER", actual=finalize)
    else:
        failed(report, "FINALIZE_MARKER_IS_SERVER_SIDE_MASTER",
               f"expected recording_finalizer.master, got {finalize}",
               expected="recording_finalizer.master", actual=finalize)

    # storage_path points at master (on the audio media_file)
    sp = audio_mf.get("storage_path") or rec.get("storage_path") or ""
    report.storage_path = sp
    if sp.endswith("/audio/master.webm") or sp.endswith("/audio/master.wav"):
        passed(report, "STORAGE_PATH_AT_MASTER", actual=sp)
    else:
        failed(report, "STORAGE_PATH_AT_MASTER",
               f"storage_path does not end at master.{{webm|wav}}: {sp}",
               expected="*/audio/master.{webm|wav}", actual=sp)

    rid = rec.get("id") or rec.get("recording_id")
    presigned_url: Optional[str] = None
    if rid is not None and audio_mf:
        mf_id = audio_mf.get("id")
        try:
            dl = http("GET", f"{gateway}/recordings/{rid}/media/{mf_id}/download",
                      token=token, timeout=15)
            url = dl.get("url") if isinstance(dl, dict) else None
            if url:
                presigned_url = url
                p = urllib.parse.urlparse(url).path
                if p.endswith("/audio/master.webm") or p.endswith("/audio/master.wav"):
                    passed(report, "DOWNLOAD_URL_POINTS_AT_MASTER", actual=p)
                else:
                    failed(report, "DOWNLOAD_URL_POINTS_AT_MASTER",
                           f"download url path does not point at master: {p}", actual=p)
            else:
                skipped(report, "DOWNLOAD_URL_POINTS_AT_MASTER", "no url in response")
        except HttpError as e:
            skipped(report, "DOWNLOAD_URL_POINTS_AT_MASTER", f"HTTP {e.status}")
    else:
        skipped(report, "DOWNLOAD_URL_POINTS_AT_MASTER", "no audio media_file in recording")

    # master playable: size + duration via /raw (gateway-proxied; externally reachable).
    # Helm presigned URL is *internal* (vexa-vexa-minio:9000) so it's not usable from off-cluster.
    # /raw is mounted on the public gateway and passes through to MinIO.
    raw_url = None
    if rid is not None and audio_mf:
        mf_id = audio_mf.get("id")
        raw_url = f"{gateway}/recordings/{rid}/media/{mf_id}/raw"
    if raw_url:
        cl = ranged_size(raw_url, token)
        if cl is not None:
            report.master_size_bytes = cl
            if cl >= 100_000:
                passed(report, "MASTER_SIZE_PLAUSIBLE",
                       f"{cl} bytes (≥100KB)", actual=cl)
            else:
                failed(report, "MASTER_SIZE_PLAUSIBLE",
                       f"master is {cl} bytes — looks like a Pack-M fragment regression",
                       expected=">=100000", actual=cl)
        else:
            skipped(report, "MASTER_SIZE_PLAUSIBLE", "size probe failed via /raw")

        rprobe = ffprobe_duration_authed(raw_url, token)
        if rprobe is not None:
            report.master_duration_sec = rprobe
            expected_min = max(report.duration_sec * 0.50, 30)
            if rprobe >= expected_min:
                passed(report, "MASTER_DURATION_PLAUSIBLE",
                       f"{rprobe:.1f}s (>={expected_min:.0f}s)", actual=rprobe)
            else:
                failed(report, "MASTER_DURATION_PLAUSIBLE",
                       f"duration {rprobe:.1f}s below threshold {expected_min:.0f}s",
                       expected=f">={expected_min:.0f}", actual=rprobe)
        else:
            skipped(report, "MASTER_DURATION_PLAUSIBLE", "ffprobe returned no duration")
    else:
        skipped(report, "MASTER_SIZE_PLAUSIBLE", "no recording id / media file")
        skipped(report, "MASTER_DURATION_PLAUSIBLE", "no recording id / media file")

    # Pack U unified-alignment: last segment.end ≤ master_duration (within tolerance)
    segs: list[dict] = []
    try:
        tx = http("GET", f"{gateway}/transcripts/{PLATFORM_NATIVE_KEY[report.platform]}/{report.native_id}",
                  token=token, timeout=20)
        if isinstance(tx, dict):
            segs = tx.get("segments") or []
    except HttpError as e:
        log(f"  /transcripts fetch failed: HTTP {e.status}")

    if segs:
        last_end = max(float(s.get("end_time") or s.get("end") or 0) for s in segs)
        if report.master_duration_sec is not None:
            if last_end <= report.master_duration_sec + 5.0:
                passed(report, "SEGMENT_FITS_AUDIO_TIMELINE",
                       f"last_seg_end={last_end:.1f}s ≤ master_dur+5={report.master_duration_sec+5.0:.1f}s")
            else:
                failed(report, "SEGMENT_FITS_AUDIO_TIMELINE",
                       f"last_seg_end={last_end:.1f}s > master_dur+5={report.master_duration_sec+5.0:.1f}s — alignment hook regression",
                       expected=f"<={report.master_duration_sec+5.0:.1f}", actual=last_end)
        else:
            skipped(report, "SEGMENT_FITS_AUDIO_TIMELINE", "master_duration unknown")
    else:
        skipped(report, "SEGMENT_FITS_AUDIO_TIMELINE", "no transcript segments returned")

    # Pack FM-274 hallucination corpus — transcript should NOT contain the canonical phrases
    canonical_hallucinations = [
        "thanks for watching",
        "subscribe to my channel",
        "see you in the next video",
        "thank you for watching",
    ]
    if segs:
        leaked = []
        for s in segs:
            text = (s.get("text") or "").lower().strip()
            for h in canonical_hallucinations:
                if h in text:
                    leaked.append((h, text))
        if leaked:
            failed(report, "NO_HALLUCINATION_PHRASES",
                   f"found {len(leaked)} canonical-hallucination phrase(s) in transcript: {leaked[:3]}",
                   actual=leaked)
        else:
            passed(report, "NO_HALLUCINATION_PHRASES", f"checked {len(segs)} segments")
    else:
        skipped(report, "NO_HALLUCINATION_PHRASES", "no transcript segments to scan")

    # Pack O bot_logs JSONB present (if compose/helm gave us a SIGKILL'd or completed bot)
    bl = data.get("bot_logs") or data.get("bot_stdout")
    if isinstance(bl, (str, list)) and bl:
        passed(report, "BOT_LOGS_FIELD_PRESENT",
               f"{len(bl) if isinstance(bl, list) else len(bl.encode())} bytes/entries")
    else:
        # Pack O DoD has helm/compose mode — if absent, this is a soft skip (Pack O may not be live yet)
        skipped(report, "BOT_LOGS_FIELD_PRESENT", "data.bot_logs absent — Pack O may be off in this deployment")

    # Pack T bot_resources JSONB present
    br = data.get("bot_resources")
    if isinstance(br, dict) and br:
        peak = br.get("peak_memory_bytes")
        if peak:
            passed(report, "BOT_RESOURCES_FIELD_PRESENT",
                   f"peak_memory={peak} samples={br.get('sample_count')}")
        else:
            passed(report, "BOT_RESOURCES_FIELD_PRESENT", "field present (no peak yet)")
    else:
        skipped(report, "BOT_RESOURCES_FIELD_PRESENT", "data.bot_resources absent — Pack T may be off")

    # Pack M chunk-count vs duration: chunk_count lives on media_files[].chunk_count after finalize
    n = audio_mf.get("chunk_count")
    if n is not None and report.master_duration_sec:
        per_minute = n / max(report.master_duration_sec / 60.0, 0.1)
        if 0.5 <= per_minute <= 30.0:
            passed(report, "CHUNK_RATE_PLAUSIBLE",
                   f"{n} chunks / {report.master_duration_sec:.1f}s = {per_minute:.1f}/min")
        else:
            failed(report, "CHUNK_RATE_PLAUSIBLE",
                   f"{n} chunks over {report.master_duration_sec:.1f}s = {per_minute:.1f}/min — out of band",
                   actual=per_minute)
    else:
        skipped(report, "CHUNK_RATE_PLAUSIBLE", "chunk_count metadata absent")


def ffprobe_duration(url: str) -> Optional[float]:
    """Run ffprobe on a URL and return duration in seconds, or None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", url],
            capture_output=True, text=True, timeout=60,
        )
        v = out.stdout.strip()
        return float(v) if v else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def ffprobe_duration_authed(url: str, token: str) -> Optional[float]:
    """ffprobe an authed URL — try format duration, then last-packet pts as fallback.

    WebM written by MediaRecorder often lacks a duration in its EBML header
    (browser writes it lazily). For those, we read the last packet's pts_time.
    """
    headers = f"X-API-Key: {token}\r\n"

    # Strategy 1: format-level duration
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-headers", headers,
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", url],
            capture_output=True, text=True, timeout=60,
        )
        v = out.stdout.strip()
        if v and v != "N/A":
            return float(v)
    except FileNotFoundError:
        return None
    except Exception:
        pass

    # Strategy 2: last packet timestamp (slow — counts every packet)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-headers", headers,
             "-select_streams", "a", "-show_entries", "packet=pts_time",
             "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=180,
        )
        last = ""
        for line in out.stdout.splitlines():
            line = line.strip().rstrip(",")
            if line and line != "N/A":
                last = line
        if last:
            return float(last)
    except Exception:
        pass

    # Strategy 3: packet count × default Opus frame size (20ms)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-headers", headers,
             "-count_packets", "-select_streams", "a",
             "-show_entries", "stream=nb_read_packets",
             "-of", "default=nw=1:nk=1", url],
            capture_output=True, text=True, timeout=180,
        )
        n = out.stdout.strip()
        if n.isdigit():
            return int(n) * 0.020  # ~20ms per Opus packet
    except Exception:
        pass

    return None


def ranged_size(url: str, token: str) -> Optional[int]:
    """Get total size of resource via Range: bytes=0-0 + Content-Range header."""
    try:
        req = urllib.request.Request(
            url,
            headers={"X-API-Key": token, "Range": "bytes=0-0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            cr = resp.headers.get("Content-Range", "")
            # Format: "bytes 0-0/<total>"
            if "/" in cr:
                tail = cr.split("/")[-1].strip()
                if tail.isdigit():
                    return int(tail)
        # fallback: GET full body, return byte count
        req2 = urllib.request.Request(url, headers={"X-API-Key": token}, method="GET")
        with urllib.request.urlopen(req2, timeout=120) as resp:
            return len(resp.read())
    except Exception:
        return None


# ─── chunk-leak (Pack M) memory-pattern verifier (uses bot pod RSS) ─────

def sample_bot_rss(deployment: str, native_id: str) -> Optional[int]:
    """Best-effort: get bot pod/container RSS in bytes."""
    if deployment == "compose":
        try:
            cl = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=vexa-bot-"],
                capture_output=True, text=True, timeout=10,
            )
            for name in cl.stdout.splitlines():
                ins = subprocess.run(
                    ["docker", "inspect", "--format", "{{json .Config.Env}}", name],
                    capture_output=True, text=True, timeout=10,
                )
                if native_id in ins.stdout:
                    s = subprocess.run(
                        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
                        capture_output=True, text=True, timeout=10,
                    )
                    txt = s.stdout.strip().split("/")[0].strip()
                    return parse_memsize(txt)
        except Exception:
            return None
    if deployment == "helm":
        try:
            kc = state_path("helm") / "lke_kubeconfig"
            pods = subprocess.run(
                ["kubectl", "--kubeconfig", str(kc), "get", "pods", "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(pods.stdout)
            for p in data.get("items", []):
                envs = p.get("spec", {}).get("containers", [{}])[0].get("env", [])
                if any(native_id in str(e.get("value", "")) for e in envs):
                    pn = p["metadata"]["name"]
                    top = subprocess.run(
                        ["kubectl", "--kubeconfig", str(kc), "top", "pod", pn, "--no-headers"],
                        capture_output=True, text=True, timeout=15,
                    )
                    parts = top.stdout.split()
                    if len(parts) >= 3:
                        return parse_memsize(parts[2])
        except Exception:
            return None
    if deployment == "lite":
        ip = read_state("lite", "vm_ip")
        if not ip:
            return None
        try:
            cl = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}",
                 f"docker ps --format '{{{{.Names}}}}' --filter name=vexa-bot- | while read n; do "
                 f"docker inspect --format '{{{{json .Config.Env}}}}' $n | grep -q {shlex.quote(native_id)} && "
                 f"docker stats --no-stream --format '{{{{.MemUsage}}}}' $n | awk -F'/' '{{print $1}}' && break; done"],
                capture_output=True, text=True, timeout=15,
            )
            return parse_memsize(cl.stdout.strip())
        except Exception:
            return None
    return None


def parse_memsize(s: str) -> Optional[int]:
    s = s.strip().replace(" ", "")
    if not s:
        return None
    units = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3,
             "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    for u in sorted(units.keys(), key=len, reverse=True):
        if s.upper().endswith(u):
            try:
                return int(float(s[:-len(u)]) * units[u])
            except ValueError:
                return None
    try:
        return int(float(s))
    except ValueError:
        return None


def verify_memory_pattern(report: Report) -> None:
    """Pack M chunk-leak guard: bot RSS should not grow unbounded.

    Tolerate baseline + slope. Fail if growth > 5MB/min sustained over 3+ samples.
    """
    rss_samples = [s for s in report.samples if s.get("rss_bytes")]
    if len(rss_samples) < 3:
        skipped(report, "BOT_MEMORY_BOUNDED", f"only {len(rss_samples)} RSS samples")
        return
    # linear fit slope (bytes/sec)
    xs = [s["t"] for s in rss_samples]
    ys = [s["rss_bytes"] for s in rss_samples]
    t0 = xs[0]
    xs = [x - t0 for x in xs]
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        skipped(report, "BOT_MEMORY_BOUNDED", "samples too clustered for slope fit")
        return
    slope = (n * sxy - sx * sy) / denom  # bytes/sec
    slope_mb_min = slope * 60 / (1024 * 1024)
    if slope_mb_min < 5.0:
        passed(report, "BOT_MEMORY_BOUNDED",
               f"RSS slope={slope_mb_min:+.2f} MB/min over {n} samples (within ±5)")
    else:
        failed(report, "BOT_MEMORY_BOUNDED",
               f"RSS slope={slope_mb_min:+.2f} MB/min — chunk-leak suspected",
               expected="<5 MB/min", actual=slope_mb_min)


# ─── corpus presence check (Pack FM-274) ────────────────────────────────

def verify_corpus_in_bot_image(report: Report) -> None:
    """Pack FM-274: hallucination corpus files shipped in the bot image.

    Inspects a *running* bot pod/container — works regardless of whether
    that bot is from this run or another concurrent run. Same image either way.
    """
    files = ["en.txt", "es.txt", "pt.txt", "ru.txt"]
    corpus_path = "/app/vexa-bot/core/dist/services/hallucinations/"

    def _check_via(cmd: list[str], label: str) -> bool:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            present = r.stdout.split()
            missing = [f for f in files if f not in present]
            if not missing:
                passed(report, "HALLUCINATION_CORPUS_IN_IMAGE",
                       f"{label}: all 4 files present")
                return True
            failed(report, "HALLUCINATION_CORPUS_IN_IMAGE",
                   f"{label} missing: {missing}", actual=present)
            return True
        except Exception as e:
            log(f"  corpus check via {label} failed: {e}")
            return False

    if report.deployment == "compose":
        # find any vexa-bot container
        try:
            cl = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=vexa-bot-"],
                capture_output=True, text=True, timeout=10,
            )
            names = [n for n in cl.stdout.splitlines() if n]
            if not names:
                skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE",
                        "no vexa-bot container running on compose")
                return
            if _check_via(["docker", "exec", names[0], "ls", corpus_path], f"docker exec {names[0]}"):
                return
        except Exception as e:
            skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE", f"{e}")
            return

    if report.deployment == "helm":
        kc = state_path("helm") / "lke_kubeconfig"
        if not kc.is_file():
            skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE", "no kubeconfig in state")
            return
        try:
            # find any Running pod whose image is vexaai/vexa-bot
            pods = subprocess.run(
                ["kubectl", "--kubeconfig", str(kc), "get", "pods",
                 "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(pods.stdout)
            pn = None
            for p in data.get("items", []):
                phase = p.get("status", {}).get("phase")
                img = (p.get("spec", {}).get("containers", [{}])[0].get("image") or "")
                if phase == "Running" and "vexa-bot" in img:
                    pn = p["metadata"]["name"]
                    break
            if not pn:
                skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE",
                        "no Running vexa-bot pod found on helm")
                return
            if _check_via(
                ["kubectl", "--kubeconfig", str(kc), "exec", pn, "--", "ls", corpus_path],
                f"kubectl exec {pn}",
            ):
                return
        except Exception as e:
            skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE", f"{e}")
            return

    if report.deployment == "lite":
        ip = read_state("lite", "vm_ip")
        if not ip:
            skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE", "no vm_ip for lite")
            return
        try:
            cl = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}",
                 "docker ps --format '{{.Names}}' --filter name=vexa-bot- | head -1"],
                capture_output=True, text=True, timeout=15,
            )
            name = cl.stdout.strip()
            if not name:
                skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE",
                        "no vexa-bot container running on lite")
                return
            if _check_via(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{ip}",
                 f"docker exec {shlex.quote(name)} ls {shlex.quote(corpus_path)}"],
                f"ssh+docker exec {name}",
            ):
                return
        except Exception as e:
            skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE", f"{e}")
            return

    skipped(report, "HALLUCINATION_CORPUS_IN_IMAGE",
            f"unsupported deployment={report.deployment}")


# ─── dashboard pagination (#304) ────────────────────────────────────────

def verify_dashboard_pagination(report: Report, gateway: str, token: str) -> None:
    """#304: /meetings paginated must dedupe by id."""
    seen = set()
    dups = 0
    total = 0
    offset = 0
    page = 50
    for _ in range(6):  # up to 6 pages
        try:
            r = http("GET", f"{gateway}/meetings?limit={page}&offset={offset}",
                     token=token, timeout=20)
        except HttpError:
            break
        items = r if isinstance(r, list) else (r.get("meetings") or r.get("items") or [])
        if not items:
            break
        for m in items:
            mid = m.get("id") or m.get("meeting_id")
            if mid is None:
                continue
            total += 1
            if mid in seen:
                dups += 1
            seen.add(mid)
        if len(items) < page:
            break
        offset += page
    if total == 0:
        skipped(report, "DASHBOARD_NO_DUPLICATE_MEETINGS", "no meetings returned")
        return
    if dups == 0:
        passed(report, "DASHBOARD_NO_DUPLICATE_MEETINGS",
               f"{total} meetings, 0 dups across {len(seen)} unique ids")
    else:
        failed(report, "DASHBOARD_NO_DUPLICATE_MEETINGS",
               f"{dups} duplicates in {total} rows — #304 regression",
               expected=0, actual=dups)


# ─── main ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["gmeet", "teams", "zoom_web"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--deployment", required=True, choices=["compose", "helm", "lite"])
    ap.add_argument("--mode", choices=["normal", "crash"], default="normal")
    ap.add_argument("--duration", type=int, default=240)
    ap.add_argument("--gateway-url", default=None)
    ap.add_argument("--api-token", default=None)
    ap.add_argument("--admin-token", default=None)
    ap.add_argument("--bot-name", default="tests3-auto")
    ap.add_argument("--output", default=None)
    ap.add_argument("--admit-timeout", type=int, default=180,
                    help="seconds to wait for admission to active")
    ap.add_argument("--callback-timeout", type=int, default=180,
                    help="seconds to wait for callback to deliver after stop")
    args = ap.parse_args()

    gateway, token, admin = resolve_endpoints(args)
    log(f"deployment={args.deployment} gateway={gateway}")
    log(f"platform={args.platform} mode={args.mode} duration={args.duration}s")

    report = Report(
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        deployment=args.deployment,
        platform=args.platform,
        mode=args.mode,
        duration_sec=args.duration,
        meeting_url=args.url,
        bot_name=args.bot_name,
    )
    report.native_id = extract_native_id(args.platform, args.url)

    # 1. dispatch
    try:
        b = dispatch_bot(gateway, token, args.platform, args.url, args.bot_name)
    except HttpError as e:
        die(f"dispatch failed: {e}", code=2)
        return 2  # unreachable
    report.meeting_internal_id = b.get("id") or b.get("meeting_id")
    log(f"dispatched: meeting_id={report.meeting_internal_id} "
        f"native={report.native_id} bot={b.get('bot_container_id') or b.get('container_id')}")

    # 2. wait for admission
    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    print(f"  ADMIT BOT NOW → {args.url}", flush=True)
    print(f"  (bot_name='{args.bot_name}', polling for status=active up to {args.admit_timeout}s)", flush=True)
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n", flush=True)
    deadline = time.time() + args.admit_timeout
    admitted = False
    while time.time() < deadline:
        m = get_meeting(gateway, token, args.platform, report.native_id)
        st = m.get("status") if m else None
        if st in ("active", "completed", "failed"):
            log(f"  admitted (status={st})")
            admitted = (st == "active")
            break
        time.sleep(5)
    if not admitted:
        passed(report, "BOT_DISPATCH_OK", "bot dispatched")
        failed(report, "BOT_REACHED_ACTIVE",
               f"timeout {args.admit_timeout}s waiting for status=active")
        report.verdict = "fail"
        write_report(report, args)
        return 1
    passed(report, "BOT_DISPATCH_OK", "bot dispatched")
    passed(report, "BOT_REACHED_ACTIVE", "admitted into meeting")

    # 3. monitor for duration
    log(f"recording {args.duration}s — sampling state every 10s")
    monitor_until = time.time() + args.duration
    while time.time() < monitor_until:
        s = sample_meeting(gateway, token, args.platform, report.native_id)
        s.rss_bytes = sample_bot_rss(args.deployment, report.native_id)
        report.samples.append({
            "t": s.t,
            "status": s.status,
            "chunk_count": s.chunk_count,
            "rss_bytes": s.rss_bytes,
        })
        log(f"    t+{int(s.t - report.samples[0]['t']):>3}s  status={s.status}  "
            f"chunks={s.chunk_count}  rss={s.rss_bytes}")
        if s.status not in ("active", None):
            log(f"  status changed to {s.status} mid-recording — breaking out")
            break
        time.sleep(10)

    # 4. terminate
    if args.mode == "normal":
        try:
            stop_bot_normal(gateway, token, args.platform, report.native_id)
            passed(report, "BOT_DELETE_OK", "DELETE /bots succeeded")
        except HttpError as e:
            failed(report, "BOT_DELETE_OK", f"HTTP {e.status}", actual=e.body[:200])
    else:  # crash
        if kill_bot_crash(args.deployment, report.native_id):
            passed(report, "BOT_SIGKILL_OK", "SIGKILL'd bot")
        else:
            failed(report, "BOT_SIGKILL_OK", "could not locate or kill bot")

    # 5. wait for callback
    deadline = time.time() + args.callback_timeout
    final_meeting = None
    while time.time() < deadline:
        m = get_meeting(gateway, token, args.platform, report.native_id)
        st = m.get("status") if m else None
        if st in ("completed", "failed"):
            final_meeting = m
            break
        time.sleep(5)
    if not final_meeting:
        failed(report, "CALLBACK_TERMINAL_REACHED",
               f"meeting never reached terminal status within {args.callback_timeout}s")
        report.verdict = "fail"
        write_report(report, args)
        return 1
    passed(report, "CALLBACK_TERMINAL_REACHED",
           f"status={final_meeting.get('status')} after {int(args.callback_timeout)}s budget")

    # 6. verify recording-side assertions
    verify_recording(report, gateway, token, final_meeting)
    verify_memory_pattern(report)
    verify_corpus_in_bot_image(report)
    verify_dashboard_pagination(report, gateway, token)

    # final verdict
    fails = sum(1 for a in report.assertions if a["status"] == "fail")
    skips = sum(1 for a in report.assertions if a["status"] == "skip")
    passes = sum(1 for a in report.assertions if a["status"] == "pass")
    report.verdict = "pass" if fails == 0 else "fail"
    log("")
    log(f"  ─── verdict: {report.verdict.upper()} ─── {passes} pass / {fails} fail / {skips} skip")

    write_report(report, args)
    return 0 if fails == 0 else 1


def write_report(report: Report, args: argparse.Namespace) -> None:
    if args.output:
        out = Path(args.output)
    else:
        rdir = state_path(args.deployment) / "reports" / "real-meeting"
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        out = rdir / f"auto-real-{args.platform}-{args.mode}-{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(report), indent=2, default=str))
    log(f"  report → {out}")


if __name__ == "__main__":
    sys.exit(main())
