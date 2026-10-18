"""tickets.py — what `report_issue` sends to the operator's ticket sink, and in which wire shape.

The tool itself (`app.py`) validates and clips what a calling agent wrote; this module bounds it,
composes the canonical ticket (summary, description, a content fingerprint and a salted caller
fingerprint — never the caller's key), and maps it onto the sink the operator chose: `raw` (the
canonical payload to an opaque webhook) or `github` (GitHub's issue API).
"""
from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .app import ReportIssue

# --- report_issue (agent-filed tickets) -------------------------------------
# Caps are defensive, not cosmetic: this route forwards caller-supplied text to an
# operator webhook, so every field is bounded before it leaves the process.
_MAX_TEXT_CHARS = 2000
_MAX_LOGS_CHARS = 4000
# Linode's ticket shape (POST /v4/support/tickets): summary 1-64, description 1-65,000. We store
# the same canonical pair so the MCP tool, the future API endpoint and the docs form all land one
# shape in the sink — the agent-facing arguments below are composed into it server-side.
_MAX_SUMMARY_CHARS = 64
# Whole-body ceiling. The handler caps every field, but a caller can still push megabytes at the
# JSON parser; this refuses before parsing. NOTE this is the HANDLER's cap — the public
# (key-less) door must ALSO carry a body cap + per-IP limit at the GATEWAY layer (see README).
_MAX_BODY_BYTES = 64 * 1024
_FINGERPRINT_SAMPLE_CHARS = 200
# Default salt for the caller fingerprint. A deployment SHOULD set
# VEXA_TICKET_FINGERPRINT_SALT so fingerprints are not comparable across deployments.
_DEFAULT_CALLER_SALT = "vexa-mcp-report-issue"


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Trim + hard-cap a caller-supplied string. Returns None for empty/blank input."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _fingerprint(deployment: str, what_happened: str) -> str:
    """Stable dedupe key: deployment + the first 200 chars of what_happened."""
    material = f"{deployment.strip().lower()}|{what_happened.strip()[:_FINGERPRINT_SAMPLE_CHARS]}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _summary_of(what_happened: str) -> str:
    """The Linode-shaped `summary` (1-64 chars): the first line/clause of what happened."""
    first_line = what_happened.strip().splitlines()[0].strip()
    if len(first_line) <= _MAX_SUMMARY_CHARS:
        return first_line
    return first_line[: _MAX_SUMMARY_CHARS - 1].rstrip() + "\u2026"


def _description_of(data: "ReportIssue") -> str:
    """The Linode-shaped `description`: the whole story, composed from the agent's answers."""
    parts = [f"What I tried:\n{data.what_i_tried}", f"What happened:\n{data.what_happened}"]
    where = data.deployment + (f" {data.version}" if data.version else "")
    parts.append(f"Deployment: {where}")
    if data.native_meeting_id:
        parts.append(f"Meeting: {data.platform or 'unknown platform'} / {data.native_meeting_id}")
    if data.logs:
        parts.append(f"Logs:\n{data.logs}")
    return "\n\n".join(parts)


# --- ticket sink adapters ----------------------------------------------------
# The sink is an OPERATOR surface. `raw` (the default) posts the canonical ticket payload to an
# opaque webhook — byte-for-byte what self-hosters already get. `github` maps the same payload
# onto GitHub's issue API so a deployment can use an issue tracker it already runs as the sink,
# with no new infrastructure. Nothing about the payload's construction changes between the two;
# only the wire shape of this one hop does.
_SINK_FORMAT_RAW = "raw"
_SINK_FORMAT_GITHUB = "github"
_DEFAULT_SINK_LABELS = "state: incoming"
_GITHUB_API_VERSION = "2022-11-28"


def _sink_format() -> str:
    """Operator-selected wire shape for the sink hop. Unknown/unset → `raw` (today's behaviour)."""
    value = (os.getenv("VEXA_TICKET_SINK_FORMAT") or "").strip().lower()
    return _SINK_FORMAT_GITHUB if value == _SINK_FORMAT_GITHUB else _SINK_FORMAT_RAW


def _sink_labels() -> List[str]:
    """Labels applied to a github-format ticket. Comma-separated; default `state: incoming`."""
    raw = os.getenv("VEXA_TICKET_SINK_LABELS")
    if raw is None or not raw.strip():
        raw = _DEFAULT_SINK_LABELS
    return [label.strip() for label in raw.split(",") if label.strip()]


def _github_issue_body(payload: Dict[str, Any]) -> str:
    """Render the canonical ticket payload as the markdown body of a GitHub issue.

    Every field the sink would have received in `raw` appears here — nothing is dropped, because
    the issue IS the ticket on this deployment. The meeting join key gets its own heading: it is
    what lines the reporter's account up against our own record of the same meeting.
    """
    lines: List[str] = []
    lines.append("_Filed by a calling agent through the Vexa MCP `report_issue` tool._")
    lines.append("")
    lines.append("### What I tried")
    lines.append(str(payload.get("what_i_tried") or "—"))
    lines.append("")
    lines.append("### What happened")
    lines.append(str(payload.get("what_happened") or "—"))
    lines.append("")
    lines.append("### Join key")
    if payload.get("native_meeting_id"):
        lines.append(f"- **native_meeting_id:** `{payload['native_meeting_id']}`")
        lines.append(f"- **platform:** `{payload.get('platform') or 'unknown'}`")
        entity = payload.get("entity")
        if isinstance(entity, dict):
            lines.append(f"- **resolved entity:** `{entity.get('type')}` → `{entity.get('url')}`")
        else:
            lines.append("- **resolved entity:** none (not owned by the calling key, or not found)")
    else:
        lines.append("- none supplied — this ticket is not bound to a meeting.")
    lines.append("")
    lines.append("### Deployment")
    lines.append(f"- **deployment:** {payload.get('deployment')}")
    lines.append(f"- **version:** {payload.get('version') or 'not stated'}")
    lines.append(f"- **severity:** {payload.get('severity') if payload.get('severity') is not None else 'not stated'}")
    lines.append("")
    if payload.get("logs"):
        lines.append("### Logs")
        truncated = " (truncated server-side)" if payload.get("logs_truncated") else ""
        lines.append(f"Pasted by the reporting agent{truncated}:")
        lines.append("")
        lines.append("```")
        lines.append(str(payload["logs"]))
        lines.append("```")
        lines.append("")
    lines.append("### Provenance")
    lines.append(f"- **source:** `{payload.get('source')}` · **tool:** `{payload.get('tool')}`")
    lines.append(f"- **reported_at:** `{payload.get('reported_at')}`")
    lines.append(f"- **fingerprint:** `{payload.get('fingerprint')}` (content-derived, for dedupe)")
    lines.append(
        f"- **caller_fingerprint:** `{payload.get('caller_fingerprint')}` "
        "(salted hash of the calling key — never the key itself)"
    )
    return "\n".join(lines)


def _sink_request(payload: Dict[str, Any], sink_token: str) -> tuple:
    """(headers, json_body) for the sink hop, per VEXA_TICKET_SINK_FORMAT.

    `raw` is byte-unchanged from before the switch existed: the canonical payload, with an
    optional bearer token. `github` maps it onto `{title, body, labels}` with GitHub's headers.
    """
    if _sink_format() == _SINK_FORMAT_GITHUB:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }
        if sink_token:
            headers["Authorization"] = f"Bearer {sink_token}"
        body: Dict[str, Any] = {
            "title": payload.get("summary"),
            "body": _github_issue_body(payload),
        }
        labels = _sink_labels()
        if labels:
            body["labels"] = labels
        return headers, body

    headers = {"Content-Type": "application/json"}
    if sink_token:
        headers["Authorization"] = f"Bearer {sink_token}"
    return headers, payload


def _caller_fingerprint(api_key: str) -> str:
    """A pseudonymous, stable handle for the caller — NEVER the API key itself.

    Deliberate choice: the ticket sink is an operator surface, not an auth boundary, so it
    receives a salted keyed FINGERPRINT of the key instead of the credential. That is enough to
    join two tickets from the same account (and, with the same salt, to match an account
    server-side) while a leak of the sink or its logs leaks no usable Vexa credential.
    The raw key is forwarded to the GATEWAY only, exactly as every other tool does. The raw key
    is never stored, logged, or returned.

    ``blake2b`` keyed with the deployment salt, not a bare SHA-256: this is a keyed fingerprint,
    not password storage (there is nothing here to verify a secret AGAINST), and the keyed
    construction is the right primitive for it — a plain digest of a low-entropy input is
    guessable by whoever holds the salt-free hash.
    """
    salt = os.getenv("VEXA_TICKET_FINGERPRINT_SALT") or _DEFAULT_CALLER_SALT
    # codeql[py/weak-sensitive-data-hashing] lgtm[py/weak-sensitive-data-hashing]: this is a keyed
    # fingerprint of a credential used as a sink handle, never password storage or verification —
    # the key is the secret and the digest is never compared against user input; a slow hash here
    # would only slow every ticket. Reviewed 2026-09-05 (v0.12.27 car 11).
    return hashlib.blake2b(api_key.encode("utf-8"), key=salt.encode("utf-8")[:64], digest_size=8).hexdigest()  # codeql[py/weak-sensitive-data-hashing] lgtm[py/weak-sensitive-data-hashing] keyed fingerprint, not password storage — see the note above
