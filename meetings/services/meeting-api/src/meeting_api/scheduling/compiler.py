"""O-MTG-3 · the scheduling compiler — `ScheduledBot{cron|at}` → a `schedule.v1` job.

The seam (verbatim plan intent): a user-facing scheduling intent (`ScheduledBot`) compiles
into a `schedule.v1` ScheduleJob whose `request` is the real `POST /bots` call (the bot-spawn
request, body = the parent meeting-api's `MeetingCreate` shape). When the job fires, the ACTION
is "issue the captured POST /bots request" — production does the HTTP; the eval captures it.

We conform to the `schedule.v1` contract EXACTLY (we do not invent a new one): the emitted job
is validated AT THE SEAM (jsonschema by path against the unsealed-in-dev
`runtime/contracts/schedule.v1/schedule.schema.json`, the `runtime/tests` `_conforms`
discipline mirrored from the lifecycle receiver) before it is handed to the scheduler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema
from referencing import Registry, Resource

# The meeting-api endpoint a scheduled bot fires against. Mirrors the parent's
# `@router.post("/bots", ...)` in `services/meeting-api/meeting_api/meetings.py`.
DEFAULT_BOTS_URL = "http://meeting-api:8080/bots"


# --- schedule.v1 schema-by-path seam (mirrors lifecycle receiver's _load/conforms) --------

def _load_schedule_schema() -> dict:
    """Locate the schedule.v1 schema by walking up to the monorepo root.

    Loaded BY PATH (not imported) so the compiler validates against the exact published
    contract the runtime scheduler consumes — the SSOT for this brick.
    """
    rel = Path("runtime") / "contracts" / "schedule.v1" / "schedule.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _load_schedule_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def conforms(obj: Dict[str, Any], shape: str = "ScheduleJob") -> None:
    """Validate `obj` against `schedule.v1#/$defs/<shape>` (raises on non-conformance)."""
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


# --- the user-facing scheduling intent ----------------------------------------------------

@dataclass
class ScheduledBot:
    """A user's request to schedule a bot for a meeting — one-shot (`at`) or recurring (`cron`).

    `bot` is the `POST /bots` request body (the parent meeting-api's `MeetingCreate` shape):
    at minimum `{platform, native_meeting_id}`, plus optional `bot_name`, `language`, ….
    Exactly one of `at` | `cron` must be set (mirrors `schedule.v1` ScheduleJob's `oneOf`).
    """

    bot: Dict[str, Any]
    at: Optional[Any] = None          # one-shot: unix epoch seconds or ISO-8601 string
    cron: Optional[str] = None        # recurring: 5-field cron expression
    bots_url: str = DEFAULT_BOTS_URL
    api_key: Optional[str] = None     # → X-API-Key header on the POST /bots call
    idempotency_key: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.at is None) == (self.cron is None):
            raise ValueError("ScheduledBot requires exactly one of `at` or `cron`")
        if not self.bot or not isinstance(self.bot, dict):
            raise ValueError("ScheduledBot.bot must be a non-empty POST /bots request body")


def compile_scheduled_bot(sched: ScheduledBot) -> Dict[str, Any]:
    """Compile a `ScheduledBot{cron|at}` → a `schedule.v1` ScheduleJob (validated at the seam).

    The job's `request` is the captured `POST /bots` call: method POST, the meeting-api `/bots`
    URL, JSON content-type (+ optional `X-API-Key`), and `body` = the bot-spawn request body.
    `at` → `execute_at` (one-shot); `cron` → `cron` (re-arms after each run).
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if sched.api_key:
        headers["X-API-Key"] = sched.api_key

    request: Dict[str, Any] = {
        "method": "POST",
        "url": sched.bots_url,
        "headers": headers,
        "body": sched.bot,
    }

    job: Dict[str, Any] = {
        "request": request,
        "metadata": {"source": "meeting-api", **sched.metadata},
    }
    if sched.cron is not None:
        job["cron"] = sched.cron
    else:
        job["execute_at"] = sched.at
    if sched.idempotency_key is not None:
        job["idempotency_key"] = sched.idempotency_key

    # Validate the emitted job against schedule.v1#/$defs/ScheduleJob before it leaves the
    # compiler — non-conformance is a hard error, never a silently-malformed job.
    conforms(job, "ScheduleJob")
    return job
