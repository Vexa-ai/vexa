"""Routines are flows (ADR-0046 decisions 2 and 5): the one-time migration and the deprecated adapter.

A routine is a person's own flow — a schedule trigger and the `agent_run` step — owned by flows,
fired by flows' worker, and run as a turn in the routine's own chat thread. There is no other routine
mechanism: agent-api compiles nothing to the runtime scheduler and reads no `routines/*.md` file to
arm anything. What is left here is deliberately small:

* `FlowsClient` — the flows-api calls the `/api/routines` adapter needs, with the OPERATOR key,
  writing for a NAMED person (`scope: user:<uid>`), the one write ADR-0046 decision 5 lets the
  operator key make on somebody's behalf. The uid is always the caller's own, from the signed identity.
* `migrate` — the ONE-TIME move of what the old mechanism held into user flows: every
  `routines/<name>.md` file and every routine job left on the runtime scheduler. Each file is
  converted once and then RETIRED: removed from the workspace, a copy kept in the store's
  dot-namespace (`<store>/.routines/retired/<subject>/`), outside every workspace mount. A file whose
  intent one of the person's flows already has is MERGED into that flow (the flow kept, the newer
  schedule carried). A file that cannot be read, or that flows refuses, runs nothing and is SHOWN —
  a card in the Routines list with the exact reason. A file nobody stood behind (an unwatched run's
  write) becomes a paused flow the person resumes. Logs carry counts, never a routine's text.
* `start_migration` — runs `migrate` at startup and again every interval until one pass completes
  with nothing left to move, then stops for good (`<store>/.routines/migration.v1.json`).
* `card_from_flow` / `problem_cards` — what the terminal's routine list reads, off flows.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger(__name__)

TIMEOUT_S = 5.0
#: WHERE A DRAFT IS TURNED ON, in the words an agent repeats to its person (live app.dev 2026-10-18:
#: an agent sent the founder to "the Routines board", which shell2 does not have). One spelling,
#: shared with flows' `APPROVAL_NOTE`.
WHERE_TO_TURN_ON = ('the "Draft routine · <name> · Turn on" line at the top of the chat it runs in, '
                    'or its switch under Routines in the sidebar')
TURN_ON_INSTRUCTION = ("Turning a routine on is your person's own press, from their signed-in session. "
                       f"Tell them it is waiting for them: {WHERE_TO_TURN_ON}. Do not retry it "
                       "another way.")
#: The id prefix a flow-backed routine card carries.
FLOW_ID_PREFIX = "flow:"
#: The id prefix of a card for a routine file the migration could not turn into a flow.
FILE_ID_PREFIX = "file:"
#: The release the deprecated `/api/routines` adapter is removed in.
ADAPTER_ENDS = "0.14.0"
ROUTINES_DIR = "routines"
STATE_DIR = ".routines"
MARKER_DIR = ".routines/migrated"
RETIRED_DIR = ".routines/retired"
FLOW_STATE_DIR = ".routines/flows"
DONE_MARKER = ".routines/migration.v1.json"
#: What the old mechanism's job metadata said a file-armed job came from.
WORKSPACE_ROUTINE_SOURCE = "workspace-routine"
#: A routines folder's README is the folder's own description, never a routine.
NOT_ROUTINES = {"readme.md"}
NOTE_AUTHOR = "vexa"
_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_FRONTMATTER = re.compile(r"^\s*---[^\S\n]*\n(.*?)\n---[^\S\n]*(?:\n(.*))?\Z", re.DOTALL)
_OPEN_ONLY = re.compile(r"^\s*---[^\S\n]*\n(.*)\Z", re.DOTALL)

def flows_base() -> str:
    return (os.environ.get("VEXA_FLOWS_API_URL") or "").rstrip("/")


def flows_key() -> str:
    return (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()


def configured() -> bool:
    """Flows owns routines exactly when this service can reach flows-api with the operator key."""
    return bool(flows_base() and flows_key())


def flow_name(name: str) -> str:
    """A routine name as a flow name (flows.v1 `FlowDefinition.name`)."""
    cleaned = _NAME.sub("-", str(name or "").strip()).strip("-._") or "routine"
    return cleaned[:80]


class FlowsError(RuntimeError):
    def __init__(self, status: int, detail):
        super().__init__(f"flows-api answered {status}: {str(detail)[:200]}")
        self.status = status
        self.detail = detail


@dataclass
class FlowsClient:
    base: str = field(default_factory=flows_base)
    key: str = field(default_factory=flows_key)

    def _call(self, method: str, path: str, body: Optional[dict] = None,
              headers: Optional[dict] = None):
        req = urllib.request.Request(
            f"{self.base}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "X-Flows-Operator-Key": self.key,
                     "X-Actor": "agent-api:routines", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — operator-configured
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail")
            except ValueError:
                detail = None
            raise FlowsError(e.code, detail) from e

    def submit(self, subject: str, name: str, cron: str, instruction: str, *,
               activate: bool = True, grant: str = "") -> dict:
        """`grant`: the person's routine grant (`ticket_mint.routine_grant`), sent when their own
        session arms the routine as it is created."""
        body = {"name": flow_name(name), "scope": f"user:{subject}",
                "trigger": {"kind": "schedule", "cron": cron}, "steps": ["agent_run"],
                "params": {"instruction": instruction}, "activate": activate}
        if grant:
            body["grant"] = grant
        return self._call("POST", "/flows", body)

    def routines(self, subject: str) -> list[dict]:
        """The person's schedule flows that run `agent_run`, newest version per name."""
        data = self._call("GET", "/flows")
        newest: dict[str, dict] = {}
        for f in data.get("flows") or []:
            if f.get("scope") != f"user:{subject}":
                continue
            # a schedule or a one-shot (`at`, flows phase 0), run by an agent or by the no-LLM bot step
            kind = (f.get("trigger") or {}).get("kind")
            # …a keyed schedule (ADR-0050: a bot before each meeting) and a sensor-fed chat too
            if not ((kind in ("schedule", "at", "relative") and
                     {"agent_run", "request_meeting_bot"} & set(f.get("steps") or []))
                    or f.get("sensor")):
                continue
            cur = newest.get(f["name"])
            if cur is None or f["version"] > cur["version"]:
                newest[f["name"]] = f
        return sorted(newest.values(), key=lambda f: f["name"])

    def runs(self, subject: str, limit: int = 100) -> list[dict]:
        """The person's own runs, newest first (`GET /flows/runs`, scoped to `user:<subject>/`)."""
        q = urllib.parse.urlencode({"subject": subject, "limit": max(1, min(int(limit), 200))})
        return list((self._call("GET", f"/flows/runs?{q}") or {}).get("runs") or [])

    def set_status(self, subject: str, name: str, version: int, action: str, *,
                   grant: str = "") -> dict:
        """`grant`: the person's routine grant, sent on a resume their own session pressed — as
        the body field `grant` and the `X-Vexa-Subject-Grant` header."""
        q = urllib.parse.urlencode({"scope": f"user:{subject}"})
        path = f"/flows/{urllib.parse.quote(name)}/{int(version)}/{action}?{q}"
        if not grant:
            return self._call("POST", path)
        from control_plane.ticket_mint import GRANT_HEADER  # noqa: PLC0415
        return self._call("POST", path, {"grant": grant}, headers={GRANT_HEADER: grant})


def card_from_flow(subject: str, f: dict, *, state: Optional[dict] = None) -> dict:
    """The routine card the terminal reads (the Routines filter, the strip, the draft line), from a
    flow. ``state`` is the person's
    flow state (`load_flow_state`): why a paused routine is paused."""
    params = f.get("params") or {}
    held = ((state or {}).get(f["name"]) or {})
    running = f.get("status") == "active"
    if f.get("status") == "draft" and not (f.get("paused_reason") or held.get("paused_reason")):
        # PROPOSED, NOT YET ARMED (D24): an agent wrote it; it runs once the person turns it on
        held = {**held, "paused_reason": "proposed by your agent — read it, then turn it on"}
    draft = f.get("status") == "draft"
    return {
        # WHY IT IS PAUSED: the reason flows recorded when the routine paused itself (its chat
        # became shared, Vexa-ai/vexa#1783), else the one this side wrote after repeated refusals.
        **({"paused_reason": f.get("paused_reason") or held["paused_reason"]}
           if not running and (f.get("paused_reason") or held.get("paused_reason")) else {}),
        **({"last_refusal": f["last_refusal"]} if f.get("last_refusal") else {}),
        **{k: f[k] for k in ("last_run", "runs_today", "runs_per_day") if f.get(k) is not None},
        "trigger": f.get("trigger"),
        "kind": f.get("kind") or ("routine" if (f.get("trigger") or {}).get("kind") == "schedule"
                                  else "event_agent"),
        "steps": list(f.get("steps") or []),
        **({"notify": params["notify"]} if params.get("notify") else {}),
        **({"wake": params["wake"]} if params.get("wake") else {}),
        **({"filter": params["filter"]} if params.get("filter") else {}),
        "id": f"{FLOW_ID_PREFIX}{f['name']}",
        "owner": subject,
        "name": f["name"],
        "cron": (f.get("trigger") or {}).get("cron"),
        "kind": "scheduled",
        "lifecycle": "once" if (f.get("trigger") or {}).get("kind") == "at" else "recurring",
        # WHAT IT MAY DO WITH NOBODY THERE (flows phase 0), in the person's words and as names.
        "grants": list(f.get("grants") or []),
        **({"may": f["may"]} if f.get("may") else {}),
        **({"next_fires": f["next_fires"]} if f.get("next_fires") is not None else {}),
        **({"sensor": f["sensor"]} if f.get("sensor") else {}),
        "plan_kind": "prompt",
        "plan_summary": params.get("instruction", ""),
        "next_run": f.get("next_run_at"),
        **({"schedule": f["schedule"]} if f.get("schedule") else {}),
        "status": f.get("status"),
        "enabled": running,
        # A DRAFT, NOT A PAUSE: an agent proposed it and nobody has turned it on yet. The terminal
        # shows it as "Draft" with its Turn on control; a paused routine has run before.
        "draft": draft,
        "flow": {"name": f["name"], "version": f["version"], "scope": f.get("scope")},
        "thread": routine_thread(f.get("scope") or f"user:{subject}", f["name"], params),
    }


def routine_thread(scope: str, name: str, params: Optional[dict] = None) -> str:
    """The chat a routine's runs land in: the chat it was created in (`params.thread`), else its own."""
    t = str((params or {}).get("thread") or "").strip()
    if t and t.lower() not in ("flow", "new") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", t):
        return t
    return thread_session(scope, name)


def thread_session(scope: str, name: str) -> str:
    """The routine's chat thread — the same digest flows' `agent_run` derives."""
    digest = hashlib.sha1(f"{scope or 'instance'}|{name}".encode()).hexdigest()[:16]
    return f"flow-{digest}"


# ── per-person flow state: why a routine is paused, and its refused-run count ─────────────────────
#
# ONE WRITER, OUTSIDE EVERY MOUNT: `<store>/.routines/flows/<subject>.json`, written only by this
# service (the migration and `routine_flow_refusals`). No worker can write it.

def _safe_subject_dir(workspaces_dir, subject: str) -> Path:
    root = Path(workspaces_dir).resolve()
    ws = (root / str(subject)).resolve()
    if ws == root or root not in ws.parents or str(subject).startswith("."):
        raise ValueError("invalid subject")
    return ws


def _state_path(workspaces_dir, sub: str, subject: str) -> Path:
    _safe_subject_dir(workspaces_dir, subject)
    return Path(workspaces_dir) / sub / f"{subject}.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n")
    tmp.replace(path)
    wpaths.notify_written(path)   # WS-S3: durable in the bucket before this returns


def load_flow_state(workspaces_dir, subject: str) -> dict:
    return _read_json(_state_path(workspaces_dir, FLOW_STATE_DIR, subject))


def save_flow_state(workspaces_dir, subject: str, data: dict) -> None:
    _write_json(_state_path(workspaces_dir, FLOW_STATE_DIR, subject), data)


def set_paused_reason(workspaces_dir, subject: str, name: str, reason: Optional[str]) -> None:
    state = load_flow_state(workspaces_dir, subject)
    entry = dict(state.get(name) or {})
    if reason:
        entry["paused_reason"] = reason
    else:
        entry.pop("paused_reason", None)
    state[name] = entry
    save_flow_state(workspaces_dir, subject, state)


# ── the routine's chat: where a routine tells its person something ─────────────────────────────────

def post_to_routine_chat(store, subject: str, name: str, text: str,
                         session: Optional[str] = None, bind: bool = True) -> bool:
    """A line in the routine's own chat thread (the session store's notes, ADR-0048 decision 5):
    every participant reads it and the routine's next turn hears it. False when there is no store."""
    if store is None:
        return False
    from control_plane import session_store as session_store_mod  # noqa: PLC0415
    from control_plane import session_sync  # noqa: PLC0415
    scope = f"user:{subject}"
    try:
        row = session_sync.resolve(store, subject, session or thread_session(scope, name))
        if row is None:
            return False
        if bind and not row.get("flow"):
            store.update(row["id"], flow=session_store_mod.flow_ref(scope, name),
                         title=(row.get("title") or name))
        store.add_note(row["id"], NOTE_AUTHOR, text)
        return True
    except Exception as exc:  # noqa: BLE001 — the note never outranks the move it reports
        log.warning("routine chat note not written (%s)", type(exc).__name__)
        return False


# ── reading an old routine file ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParsedFile:
    name: str
    enabled: bool
    cron: str
    prompt: str
    mtime: float


def routine_files(ws: Path) -> list[Path]:
    """The workspace's old routine files — regular files directly in a real `routines/`, never its
    README, never through a link."""
    return [ws / ROUTINES_DIR / n for n in wpaths.list_files_inside(ws, ROUTINES_DIR, suffix=".md")
            if n.lower() not in NOT_ROUTINES]


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"false", "no", "0", "off"}:
        return False
    return True


def parse_routine_file(path: Path) -> "tuple[Optional[ParsedFile], str]":
    """``(parsed, "")`` or ``(None, the exact reason it cannot become a flow)``. A settings block
    that was opened and never closed is still read: the person meant those settings."""
    data = wpaths.read_bytes_inside(path.parent.parent, f"{ROUTINES_DIR}/{path.name}")
    if data is None:
        return None, "the file could not be read (missing, or not a regular file)"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "the file is not UTF-8 text"
    m = _FRONTMATTER.match(text)
    raw, body = (m.group(1), (m.group(2) or "")) if m else (None, "")
    if m is None:
        o = _OPEN_ONLY.match(text)
        if o is None:
            return None, "the file has no settings block (a '---' block with cron and prompt)"
        raw = o.group(1)
    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, "the settings block is not valid YAML"
    if not isinstance(fm, dict):
        return None, "the settings block is not a set of 'key: value' lines"
    cron = fm.get("cron")
    cron = cron.strip() if isinstance(cron, str) else ""
    prompt = fm.get("prompt")
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    if not cron:
        return None, "the file names no cron (when it should run)"
    if len(cron.split()) != 5:
        return None, f"the cron '{cron}' is not five fields (minute hour day month weekday)"
    if not prompt:
        return None, "the file names no prompt (what the routine should do)"
    parts = [prompt] + ([body.strip()] if body.strip() else [])
    # through workspace_paths, so an evicted workspace is hydrated before its mtime is read (WS-S3)
    st = wpaths.stat_inside(path.parent, path.name)
    mtime = st.st_mtime if st is not None else 0.0
    return ParsedFile(path.stem, _as_bool(fm.get("enabled")), cron, "\n\n".join(parts), mtime), ""


def _content_hash(path: Path) -> Optional[str]:
    data = wpaths.read_bytes_inside(path.parent.parent, f"{ROUTINES_DIR}/{path.name}")
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _stood_behind(workspaces_dir, subject: str, path: Path, digest: Optional[str]) -> bool:
    """Did a PERSON stand behind these bytes under the old mechanism (their own write through
    agent-api, or their confirmation)? The record is read, never written, here.

    FAILS CLOSED (D24, H8). A store with no approvals record used to count every file as stood
    behind, so any routine file an agent had written armed on migration. Now no record means no
    approval: the file becomes a PAUSED flow the person reads and turns on themselves."""
    root = Path(workspaces_dir)
    if not (root / STATE_DIR / "approvals.v1.json").exists():
        return False
    approvals = _read_json(root / STATE_DIR / "approvals" / f"{subject}.json")
    return bool(digest) and approvals.get(path.stem) == digest


# ── a routine is never a file again ─────────────────────────────────────────────────────────────

ROUTINE_FILE_REFUSAL = {
    "refused": "routine_files_retired",
    "note": ("A routine is a flow now, and a file like this does nothing. Create it with the "
             "flows_submit tool: trigger {kind: schedule, cron: '<5 fields, UTC>'}, steps "
             "['agent_run'], params.instruction = what to do each run. It gets its own chat."),
}


def looks_like_routine_file(rel: str, content: str) -> bool:
    """A Markdown page whose settings block names a `cron` beside a `prompt` or `enabled` — the shape
    the retired `routines/*.md` mechanism read. Nothing reads it any more, so writing one is refused
    with the way that works instead."""
    if not str(rel or "").lower().endswith(".md"):
        return False
    m = _FRONTMATTER.match(content or "") or _OPEN_ONLY.match(content or "")
    if m is None:
        return False
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and "cron" in fm and ("prompt" in fm or "enabled" in fm)


# ── one intent, one flow ────────────────────────────────────────────────────────────────────────

def _intent_tokens(name: str) -> list[str]:
    out = []
    for t in re.split(r"[^a-z0-9]+", str(name or "").lower()):
        if not t:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def same_intent(a: str, b: str) -> bool:
    """Two routine names mean the same routine when one's words begin the other's (plurals folded):
    `check-email` and `check-emails-notify-important-ones`; never `check-email` and
    `check-calendar`, nor `weather-lisbon` and `weather-porto`."""
    ta, tb = _intent_tokens(a), _intent_tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return long_[:len(short)] == short


# ── the one-time migration ──────────────────────────────────────────────────────────────────────

@dataclass
class MigrationCounts:
    files_converted: int = 0
    files_merged: int = 0
    files_paused: int = 0
    files_refused: int = 0
    files_already: int = 0
    files_skipped: int = 0
    jobs_converted: int = 0
    jobs_cancelled: int = 0
    failures: int = 0


def read_marker(workspaces_dir, subject: str) -> dict:
    """``{"files": {name: entry}}``. A marker an earlier version wrote (``{name: sha}`` and
    ``{"!name": sha}`` for a refusal) is read into the same shape."""
    raw = _read_json(_state_path(workspaces_dir, MARKER_DIR, subject))
    if isinstance(raw.get("files"), dict):
        return raw
    files = {}
    for k, v in raw.items():
        if k.startswith("!"):
            # refused by an earlier version, which kept no reason: tried once more below, so the
            # card can say exactly why (a refusal changes nothing)
            continue
        else:
            files[k] = {"sha": v, "outcome": "converted", "flow": flow_name(k), "legacy": True}
    return {"files": files}


def _write_marker(workspaces_dir, subject: str, data: dict) -> None:
    _write_json(_state_path(workspaces_dir, MARKER_DIR, subject), data)


def _retire(workspaces_dir, subject: str, path: Path) -> str:
    """Remove the file from the workspace, keeping a copy outside every mount. Returns the copy's
    path relative to the store."""
    data = wpaths.read_bytes_inside(path.parent.parent, f"{ROUTINES_DIR}/{path.name}") or b""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rel = f"{RETIRED_DIR}/{subject}/{path.stem}.{stamp}.md"
    dest = Path(workspaces_dir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    wpaths.notify_written(dest)   # WS-S3: the retired copy is durable before the original goes
    wpaths.unlink_inside(path.parent.parent, f"{ROUTINES_DIR}/{path.name}")
    return rel


def _created(f: dict) -> float:
    try:
        return float(f.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def migrate_subject_files(subject: str, *, client: "FlowsClient", workspaces_dir,
                          counts: MigrationCounts, store=None, workspace: Optional[str] = None) -> None:
    """Convert one person's routine files into flows of theirs, once each, and retire the files.
    ``workspace`` names a shared workspace whose files become its OWNER's (``subject``) flows; by
    default the person's own desk."""
    ws = _safe_subject_dir(workspaces_dir, workspace or subject)
    key = (lambda stem: f"{workspace}:{stem}") if workspace else (lambda stem: stem)
    paths = routine_files(ws)
    if not paths:
        return
    marker = read_marker(workspaces_dir, subject)
    files = marker.setdefault("files", {})
    flows = {f["name"]: f for f in client.routines(subject)}
    parsed_all = []
    for path in paths:
        digest = _content_hash(path)
        parsed, why = parse_routine_file(path)
        parsed_all.append((path, digest, parsed, why))
    # newest first, so a merge carries the newest schedule into the flow the first one made
    parsed_all.sort(key=lambda t: -(t[2].mtime if t[2] else 0.0))
    for path, digest, parsed, why in parsed_all:
        prev = files.get(key(path.stem)) or {}
        entry = {"sha": digest, "at": time.time()}
        legacy_unbacked = prev.get("legacy") and prev.get("outcome") == "converted" and (
            prev.get("flow") not in flows)
        if prev.get("sha") == digest and prev.get("outcome") and not legacy_unbacked:
            # converted (or refused) before, at these bytes, by an earlier version that left the
            # file in place: nothing is submitted again, the file is retired now
            entry.update({k: v for k, v in prev.items() if k not in ("sha", "at")})
            counts.files_already += 1
        elif parsed is None:
            entry.update({"outcome": "refused", "reason": why})
            counts.files_refused += 1
        else:
            entry.update(_convert(subject, parsed, path, digest, client=client, flows=flows,
                                  workspaces_dir=workspaces_dir, counts=counts, store=store))
            if entry.get("outcome") == "failed":
                counts.failures += 1
                continue                                   # flows could not answer: next pass
        entry["backup"] = _retire(workspaces_dir, workspace or subject, path)
        files[key(path.stem)] = entry
        _write_marker(workspaces_dir, subject, marker)


def _convert(subject, parsed: ParsedFile, path: Path, digest, *, client, flows: dict,
             workspaces_dir, counts: MigrationCounts, store) -> dict:
    """One file into one flow: merged into the flow with its intent, or a new flow — active when
    a person stood behind it and it was on, paused otherwise. Returns the marker entry."""
    stood = _stood_behind(workspaces_dir, subject, path, digest)
    twin = next((f for n, f in sorted(flows.items()) if same_intent(n, parsed.name)), None)
    try:
        if twin is not None:
            cron = (twin.get("trigger") or {}).get("cron")
            if parsed.mtime > _created(twin) and parsed.cron != cron:
                # a newer schedule nobody stood behind is filed as a draft beside the live one,
                # never swapped into it (D24): the person's running routine keeps its own cron
                made = client.submit(subject, twin["name"], parsed.cron,
                                     (twin.get("params") or {}).get("instruction") or parsed.prompt,
                                     activate=twin.get("status") == "active" and stood)
                flows[twin["name"]] = {**twin, **made}
            counts.files_merged += 1
            post_to_routine_chat(store, subject, twin["name"],
                                 f"The routine file routines/{path.name} had the same purpose as "
                                 "this routine and was merged into it"
                                 + (f" (its newer schedule `{parsed.cron}` is kept)"
                                    if parsed.mtime > _created(twin) and parsed.cron != cron
                                    else "") + ". The file is retired; a copy is kept.")
            return {"outcome": "merged", "flow": twin["name"]}
        run = parsed.enabled and stood
        made = client.submit(subject, parsed.name, parsed.cron, parsed.prompt, activate=run)
    except FlowsError as e:
        if 400 <= e.status < 500:
            reason = (e.detail or {}).get("refused") if isinstance(e.detail, dict) else None
            log.warning("routine migration: one file for one subject refused by flows (%s)",
                        reason or e.status)
            counts.files_refused += 1
            return {"outcome": "refused", "reason": _refusal_text(e)}
        return {"outcome": "failed"}
    flows[made["name"]] = made
    if run:
        counts.files_converted += 1
        post_to_routine_chat(store, subject, made["name"],
                             f"This routine was converted from routines/{path.name}; it runs on "
                             f"`{parsed.cron}` (UTC). The file is retired; a copy is kept.")
        return {"outcome": "converted", "flow": made["name"]}
    reason = ("paused: an agent wrote this routine while nobody was watching — read it, then "
              "resume it to start it" if not stood else
              "paused: the routine file was switched off — resume it to start it")
    set_paused_reason(workspaces_dir, subject, made["name"], reason)
    counts.files_paused += 1
    post_to_routine_chat(store, subject, made["name"],
                         f"This routine was converted from routines/{path.name} and is {reason}.")
    return {"outcome": "paused", "flow": made["name"], "reason": reason}


def _refusal_text(e: "FlowsError") -> str:
    d = e.detail if isinstance(e.detail, dict) else {}
    why = str(d.get("refused") or d.get("detail") or e.detail or e.status)
    extra = {k: v for k, v in d.items() if k not in ("refused", "detail", "note")}
    tail = (" (" + ", ".join(f"{k} {v}" for k, v in sorted(extra.items())) + ")") if extra else ""
    note = f" — {d['note']}" if d.get("note") else ""
    return f"flows refused it: {why}{tail}{note}"


def problem_cards(workspaces_dir, subject: str) -> list[dict]:
    """A card for every routine file the migration could not turn into a flow, with the reason."""
    try:
        files = read_marker(workspaces_dir, subject).get("files") or {}
    except ValueError:
        return []
    return [{"id": f"{FILE_ID_PREFIX}{name}", "owner": subject, "name": name, "cron": None,
             "kind": "scheduled", "plan_kind": "prompt", "plan_summary": "", "next_run": None,
             "status": "refused", "enabled": False, "refused_reason": e.get("reason") or "",
             "backup": e.get("backup")}
            for name, e in sorted(files.items())
            if e.get("outcome") == "refused" and not e.get("dismissed")]


def dismiss_problem(workspaces_dir, subject: str, name: str) -> bool:
    marker = read_marker(workspaces_dir, subject)
    e = (marker.get("files") or {}).get(name)
    if not e or e.get("outcome") != "refused" or e.get("dismissed"):
        return False
    e["dismissed"] = True
    _write_marker(workspaces_dir, subject, marker)
    return True


def migrate_jobs(*, client: "FlowsClient", scheduler, workspaces_dir,
                 counts: MigrationCounts) -> bool:
    """Every routine job left on the runtime scheduler becomes its owner's flow, then is cancelled.
    A job a file armed is simply cancelled: the file is the routine, and it converts above. True
    when the scheduler answered and every job is gone."""
    try:
        jobs = scheduler.list_jobs(limit=1000)
    except Exception:  # noqa: BLE001 — a scheduler that cannot answer is asked again next pass
        log.warning("routine migration: the scheduler could not list its jobs; retrying next pass")
        return False
    complete = True
    for job in jobs:
        meta = job.get("metadata") or {}
        if not meta.get("routine_id"):
            continue
        owner = str(meta.get("owner") or "")
        if owner and meta.get("source") != WORKSPACE_ROUTINE_SOURCE and meta.get(
                "plan_kind") == "prompt" and meta.get("cron"):
            try:
                client.submit(owner, str(meta.get("name") or meta["routine_id"]),
                              str(meta["cron"]), str(meta.get("plan_summary") or ""))
                counts.jobs_converted += 1
            except FlowsError as e:
                if not 400 <= e.status < 500:
                    counts.failures += 1
                    complete = False
                    continue
                counts.files_refused += 1
                log.warning("routine migration: one scheduler job refused by flows (%s)", e.status)
        got = _cancel(scheduler, job)
        counts.jobs_cancelled += got
        complete = complete and bool(got)
    return complete


def _cancel(scheduler, job: dict) -> int:
    job_id = job.get("job_id")
    if not job_id:
        return 0
    try:
        scheduler.cancel_job(job_id)
        return 1
    except Exception:  # noqa: BLE001
        return 0


def scan_subjects(workspaces_dir) -> list[str]:
    root = Path(workspaces_dir)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _owners(workspaces_dir) -> list[tuple[str, Optional[str]]]:
    """``(person, shared workspace or None)`` for every directory that may hold old routine files: a
    person's desk is theirs; a shared workspace's files are its owner's (its members roster); the
    organisation layer holds none."""
    from control_plane import workspace_ids, workspace_membership  # noqa: PLC0415
    root = Path(workspaces_dir)
    out: list[tuple[str, Optional[str]]] = []
    for name in scan_subjects(root):
        kind = workspace_ids.classify(root / name)
        if kind == "desk":
            out.append((name, None))
        elif kind == "group":
            owner = next((str(m.get("subject")) for m in workspace_membership.read_members(root, name)
                          if m.get("role") == "owner" and m.get("subject")), None)
            if owner:
                out.append((owner, name))
    return out


def migrate(*, scheduler, workspaces_dir, client: Optional["FlowsClient"] = None,
            store=None) -> MigrationCounts:
    """One migration pass over every subject's files and every routine job. Counts only."""
    client = client or FlowsClient()
    counts = MigrationCounts()
    for subject, workspace in _owners(workspaces_dir):
        try:
            migrate_subject_files(subject, client=client, workspaces_dir=workspaces_dir,
                                  counts=counts, store=store, workspace=workspace)
        except Exception:  # noqa: BLE001 — one subject never stops the pass
            log.exception("routine migration: one subject failed; retrying next pass")
            counts.failures += 1
    if scheduler is not None and not migrate_jobs(client=client, scheduler=scheduler,
                                                  workspaces_dir=workspaces_dir, counts=counts):
        counts.failures += 1
    if any(vars(counts).values()):
        log.info("routines → flows: %s", vars(counts))
    return counts


@dataclass(frozen=True)
class MigrationHandle:
    thread: Optional[threading.Thread]
    stop_event: threading.Event

    def stop(self) -> None:
        self.stop_event.set()


def migration_done(workspaces_dir) -> bool:
    return (Path(workspaces_dir) / DONE_MARKER).is_file()


def start_migration(*, scheduler, workspaces_dir, interval_sec: float = 60.0, store=None,
                    client_factory: Optional[Callable[[], "FlowsClient"]] = None,
                    background: bool = True) -> Optional[MigrationHandle]:
    """THE ONE-TIME MOVE, and its end. A pass now; while anything is left (flows or the scheduler
    could not answer), another every ``interval_sec``; once a pass leaves nothing behind, the done
    marker is written and no pass ever runs again. A deployment without flows has no routines: it
    logs that once and moves nothing (the files stay where they are)."""
    stop = threading.Event()
    if migration_done(workspaces_dir):
        return None
    if not configured():
        log.warning("routines run on flows, and this deployment has no flows-api configured "
                    "(VEXA_FLOWS_API_URL, VEXA_FLOWS_API_KEY): routine files are left as they are")
        return None

    def once() -> bool:
        try:
            counts = migrate(scheduler=scheduler, workspaces_dir=workspaces_dir,
                             client=(client_factory or FlowsClient)(), store=store)
        except Exception:  # noqa: BLE001
            log.exception("routines → flows migration pass failed; retrying next pass")
            return False
        if counts.failures:
            return False
        p = Path(workspaces_dir) / DONE_MARKER
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"done_at": time.time(), "last_pass": vars(counts)}) + "\n")
        log.info("routines → flows migration complete; it will not run again")
        return True

    def loop() -> None:
        if once():
            return
        while not stop.wait(interval_sec):
            if once():
                return

    if not background:
        loop()
        return MigrationHandle(None, stop)
    if interval_sec <= 0:
        once()
        return MigrationHandle(None, stop)
    t = threading.Thread(target=loop, name="routines-to-flows", daemon=True)
    t.start()
    return MigrationHandle(t, stop)
