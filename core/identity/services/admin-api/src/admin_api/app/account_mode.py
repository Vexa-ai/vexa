"""THE ACCOUNT MODE — whether Vexa's own server-side agents work for this account at all.

Founder ruling, 2026-10-24, on people who come to Vexa through «Vexa for Claude»: *"для этих
пользователей никаких агентов вообще не надо. Слушание только у них локально… у нас они пользуются
только звонками и воркспейсами"*. Their listening runs in their own Claude Code plugin, and every
other integration is connected to their own Claude. Vexa gives them calls (bot, transcript,
recording, calendar auto-join) and workspaces (storage, sync, sharing, MCP read/write), and spends
no model on their behalf.

TWO MODES. ``standard`` (every account that exists today, and the answer for a row that says
nothing) and ``claude``. The stored field is ``users.data.account_mode``; only ``claude`` is ever
written, so "absent" and "standard" are one state.

HOW AN ACCOUNT GETS ONE. ``POST /admin/users`` stamps it on a NEW row when the sign-in door that
creates the account asks for it (``account_mode: "claude"`` on the create body: the webapp's OAuth
door does when its ``signup_source`` is ``claude-plugin`` or ``claude-connector``). It never changes an
existing account: a person who already has Vexa and later connects Claude keeps their agents. An
operator moves an account either way with ``PATCH /admin/users/{id}`` (admin token).

WHO READS IT. This service answers it on its existing doors: ``/internal/validate`` (identity.v1
``account_mode``, which the terminal reads to hide agent surfaces) and ``/internal/users/{id}/is-admin``
(agent-api's one hop for account roles). Every server-side agent start asks agent-api's
``account_mode.agents_off`` before it spends a model.

THIS MODULE IS PURE — every decision is provable without a database.
"""
from __future__ import annotations

from typing import Any, Mapping

#: The key in ``users.data``.
FIELD = "account_mode"
#: Every account Vexa's agents work for. Never stored: a row with no field is standard.
STANDARD = "standard"
#: Vexa for Claude: calls and workspaces only, no Vexa server agent.
CLAUDE = "claude"
MODES = (STANDARD, CLAUDE)


def of(data: Any) -> str:
    """The account's mode from its ``users.data``. Anything but a stored ``claude`` is standard."""
    if isinstance(data, Mapping) and data.get(FIELD) == CLAUDE:
        return CLAUDE
    return STANDARD


def agents_off(data: Any) -> bool:
    return of(data) == CLAUDE


def apply(data: Any, mode: str) -> dict:
    """``data`` with ``mode`` set: ``claude`` is stored, ``standard`` removes the field."""
    if mode not in MODES:
        raise ValueError(f"unknown account mode {mode!r}")
    out = dict(data) if isinstance(data, Mapping) else {}
    if mode == CLAUDE:
        out[FIELD] = CLAUDE
    else:
        out.pop(FIELD, None)
    return out
