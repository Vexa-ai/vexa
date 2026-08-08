"""Agent-owned meeting metadata (#1064) — the ``custom`` namespace on ``meeting.data``.

Flywheel iteration 2: an agent that dispatches a bot can attach its own classification to the
meeting (``{project, purpose, agent_owner, tags:[...], ...}``) and later filter/retrieve its
corpus by it. The metadata lives under a RESERVED ``custom`` key inside the meeting's ``data``
JSONB — a sibling of ``recording`` / ``config`` / ``docs`` that is never touched by the recording
or lifecycle writers, and is written with the same fold-into-JSONB-atomically-under-one-mutator
pattern the recordings flow uses (``recordings/service.py``), so a metadata write can never clobber
``data.recording`` / ``data.config``.

This module owns the two things the three surfaces (``POST /bots``, ``PATCH …/metadata``,
``GET /meetings?custom.*``) must share so they can never diverge:

  * :func:`validate_custom_metadata` — the request-boundary guard: a flat-ish JSON object, size-capped
    (``MAX_METADATA_BYTES``), scalar-or-flat-list values only (no nested giant blobs). Raises
    :class:`MetadataError` (a ``ValueError``) that each route maps to a 422.
  * :func:`merge_custom` — the shallow MERGE (not replace) the amend path folds into ``data['custom']``.

PURE — no IO, no DB, no FastAPI. The routes own the HTTP mapping; the store owns the row lock.
"""
from __future__ import annotations

import json
from typing import Any

#: Reserved key inside ``meeting.data`` that holds agent-attached metadata. A sibling of the
#: recording/config/docs keys — chosen so a metadata write never collides with them.
CUSTOM_NAMESPACE = "custom"

#: Hard cap on the serialized size of the ``custom`` object a single request may attach/merge. Keeps
#: the JSONB row bounded (the meetings list projects ``data`` per row) and rejects giant blobs at the
#: door rather than letting them bloat every read. 8 KiB comfortably holds a rich classification.
MAX_METADATA_BYTES = 8192

#: Max number of keys in one metadata object — a second bound so a caller cannot smuggle thousands of
#: tiny keys under the byte cap.
MAX_METADATA_KEYS = 64

#: Scalar value types allowed at a key (or as elements of a flat list value). ``bool`` is a subclass
#: of ``int`` in Python, so it is covered; ``None`` is allowed explicitly.
_SCALAR_TYPES = (str, int, float, bool, type(None))


class MetadataError(ValueError):
    """A caller-supplied ``metadata`` object failed validation. The routes map it to HTTP 422 with
    ``str(e)`` as the detail — a typed refusal at the request boundary, never a 500 deep in the DB."""


def _is_scalar(v: Any) -> bool:
    return isinstance(v, _SCALAR_TYPES)


def validate_custom_metadata(obj: Any) -> dict:
    """Validate + normalize a caller-supplied ``metadata`` object → the dict persisted under
    ``data['custom']``. Raises :class:`MetadataError` on any violation.

    The shape is deliberately FLAT-ISH so the store stays a cheap, index-friendly JSONB object and the
    surface can never become a general document store:

      * must be a JSON object (``dict``);
      * every key is a non-empty ``str``;
      * every value is a scalar (``str`` / ``int`` / ``float`` / ``bool`` / ``null``) OR a FLAT list of
        scalars (e.g. ``tags: ["sales", "q3"]``) — no nested objects, no lists-of-lists;
      * at most :data:`MAX_METADATA_KEYS` keys;
      * the serialized object is at most :data:`MAX_METADATA_BYTES` bytes.

    Returns a NEW dict (the caller's object is never mutated).
    """
    if not isinstance(obj, dict):
        raise MetadataError("metadata must be a JSON object")
    if len(obj) > MAX_METADATA_KEYS:
        raise MetadataError(
            f"metadata has {len(obj)} keys; the maximum is {MAX_METADATA_KEYS}"
        )
    out: dict = {}
    for key, value in obj.items():
        if not isinstance(key, str) or not key.strip():
            raise MetadataError("metadata keys must be non-empty strings")
        if isinstance(value, dict):
            raise MetadataError(
                f"metadata['{key}'] must be a scalar or a flat list of scalars, not a nested object"
            )
        if isinstance(value, (list, tuple)):
            if not all(_is_scalar(el) for el in value):
                raise MetadataError(
                    f"metadata['{key}'] list may contain only scalars (str/number/bool/null)"
                )
            out[key] = list(value)
        elif _is_scalar(value):
            out[key] = value
        else:
            raise MetadataError(
                f"metadata['{key}'] must be a scalar or a flat list of scalars"
            )
    # Size cap on the normalized object — the exact bytes that will land in JSONB.
    size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    if size > MAX_METADATA_BYTES:
        raise MetadataError(
            f"metadata is {size} bytes; the maximum is {MAX_METADATA_BYTES} bytes"
        )
    return out


def merge_custom(existing: Any, incoming: dict) -> dict:
    """Shallow-MERGE ``incoming`` over the existing ``data['custom']`` object (amend, not replace).

    A missing/non-dict ``existing`` starts from ``{}``. Keys in ``incoming`` overwrite; keys only in
    ``existing`` survive — so ``PATCH …/metadata {tags:[…]}`` never wipes a ``project`` attached at
    spawn. Returns a NEW dict.
    """
    base = dict(existing) if isinstance(existing, dict) else {}
    base.update(incoming)
    return base


def parse_custom_filter(query_params: Any) -> dict:
    """Extract the ``custom.<key>=<value>`` (and ``custom_<key>=<value>``) filter from a request's
    query params → ``{<key>: <value>}`` (string values, as they arrive on the wire). Empty when no
    such param is present.

    ``query_params`` is anything iterable as ``(key, value)`` pairs (Starlette ``QueryParams`` via
    ``.multi_items()`` or ``.items()``). Both the dotted (``custom.project``) and underscored
    (``custom_project``) spellings are accepted — the dotted form is canonical; the underscored form
    is the escape hatch for clients/tools where a dot in a query key is awkward.
    """
    items = query_params.multi_items() if hasattr(query_params, "multi_items") else list(query_params.items())
    out: dict = {}
    for raw_key, value in items:
        for prefix in ("custom.", "custom_"):
            if raw_key.startswith(prefix):
                key = raw_key[len(prefix):]
                if key:
                    out[key] = value
                break
    return out
