"""THE DOORS the delivery gate uses — a Protocol and the HTTP adapter (fakes live in the tests).

Kept narrow on purpose: three other lanes build the far side of these doors in parallel (S5 the card
seen/acted state in crm, S2 the SMTP transport behind ``flows_steps.notify``, S4 the room items),
and an integrator joins them. Each door below names the exact shape it expects.

crm — ``POST {VEXA_FLOWS_CRM_API_URL}/internal/cards/attention`` (internal tier), body
    ``{"org": "<org>", "person": "<uid>", "cards": ["<id>", …]}`` → ``{"cards": {"<id>":
    {"open": bool, "seen": bool, "acted": bool, "next_actor": "<uid>"|null, "title": str,
    "from_name": str, "workspace_name": str}}}``. ``seen`` is crm's per-viewer ``seen`` relation
    (founder 2026-10-16; written only by the terminal when the card is rendered in view or opened
    from a landing page). ``title``/``from_name``/``workspace_name`` are the TEMPLATE FIELDS, resolved
    for THIS recipient — what they may read, nothing more (D8: the envelope carries no content, so the
    nudge asks for its three words at send time). A card absent from the answer is gone or not theirs:
    nothing is sent. No answer at all is not evidence of anything: the row waits and asks again.

agent-api — the in-app notice: ``POST {AGENT}/internal/notifications`` with kind ``card_waiting`` /
    ``card_slipped`` / ``delivery_failed`` (answers ``{"delivered": n, "ids": [...]}``), and its read
    state: ``POST {AGENT}/internal/notifications/state`` ``{"to": "<uid>", "ids": [...]}`` →
    ``{"notices": {"<id>": {"read": bool, "acted": str|null}}}``. A room item (a mention, a join
    request, a missed call) arrives with the notice agent-api already wrote, and is seen when it is read.

identity — ``GET /internal/users/{uid}/settings`` (``notifications`` + ``timezone``) through
    ``flows_steps.common.person_settings``, and the address through ``address_for_uid``.

mail — ``flows_steps.notify.notify``: SMTP (S2's Google Workspace sender on app.dev, FD5) or Graph;
    ``VEXA_MAIL_RELAY=capture`` sends every message to the capture sink and never to a mailbox.
"""
from __future__ import annotations

from typing import Optional, Protocol

from .gate import Attention

CARD_PREFIX = "card:"


class Ports(Protocol):
    ui: str

    def person(self, uid: str) -> tuple[dict, str, str]:
        """``(raw_policy, timezone, address)``. Raises when identity cannot be asked."""

    def attention(self, uid: str, rows: list[dict]) -> dict:
        """``{ref: (Attention, template_fields) | None}`` — None: unknown right now."""

    def in_app(self, uid: str, row: dict, fields: dict) -> Optional[str]:
        """Post the in-app notice; its id, or None when it could not be written."""

    def send(self, to: str, subject: str, body: str, link: str) -> str:
        """One email through the deployment's channel. Raises on a transport failure."""

    def failed(self, owner: str, row: dict, fields: dict) -> None:
        """Tell the person waiting that the nudge could not be delivered (``delivery_failed``)."""


def card_id(ref: str) -> str:
    return ref[len(CARD_PREFIX):] if ref.startswith(CARD_PREFIX) else ""


class HttpPorts:
    """The production doors. Every call is on the internal tier; nothing here holds a person's key."""

    def __init__(self) -> None:
        import flows_config
        self.ui = (flows_config.get("VEXA_UI_URL") or "").rstrip("/")
        self.crm = (flows_config.get("VEXA_FLOWS_CRM_API_URL") or "").rstrip("/")

    def _agent(self) -> str:
        from flows_steps import common
        return (common.AGENT_API or "").rstrip("/")

    def person(self, uid: str) -> tuple[dict, str, str]:
        from flows_steps import common
        s = common.person_settings(uid)
        return (s.get("notifications") or {}), str(s.get("timezone") or ""), common.address_for_uid(uid)

    def attention(self, uid: str, rows: list[dict]) -> dict:
        from flows_steps.common import door_headers, http
        out: dict = {}
        cards: dict[str, list[dict]] = {}
        notices: list[dict] = []
        for r in rows:
            if card_id(r["ref"]):
                cards.setdefault(r.get("org") or "", []).append(r)
            else:
                notices.append(r)
        hdr = door_headers()
        for org, group in cards.items():
            if not self.crm:
                for r in group:
                    out[r["ref"]] = None
                continue
            try:
                code, body = http("POST", f"{self.crm}/internal/cards/attention", hdr,
                                  {"org": org, "person": uid, "cards": [card_id(r["ref"]) for r in group]},
                                  timeout=10)
            except Exception:  # noqa: BLE001 — unknown, never a guess
                code, body = 0, None
            got = (body or {}).get("cards") if code == 200 and isinstance(body, dict) else None
            for r in group:
                if not isinstance(got, dict):
                    out[r["ref"]] = None
                    continue
                c = got.get(card_id(r["ref"]))
                if not isinstance(c, dict):
                    out[r["ref"]] = (Attention(open=False), {})
                    continue
                mine = not c.get("next_actor") or str(c.get("next_actor")) == str(uid)
                out[r["ref"]] = (Attention(open=bool(c.get("open")) and mine, seen=bool(c.get("seen")),
                                           acted=bool(c.get("acted"))),
                                 {"title": c.get("title") or "", "who": c.get("from_name") or "",
                                  "workspace": c.get("workspace_name") or ""})
        ids = [r["notice_id"] for r in notices if r.get("notice_id")]
        state: Optional[dict] = {}
        if ids:
            state = None
            if self._agent():
                try:
                    code, body = http("POST", f"{self._agent()}/internal/notifications/state", hdr,
                                      {"to": uid, "ids": ids}, timeout=10)
                    if code == 200 and isinstance(body, dict) and isinstance(body.get("notices"), dict):
                        state = body["notices"]
                except Exception:  # noqa: BLE001
                    state = None
        for r in notices:
            names = r.get("names") or {}
            fields = {"title": names.get("title", ""), "who": names.get("who", ""),
                      "room": names.get("room", ""), "workspace": names.get("workspace", "")}
            if not r.get("notice_id"):
                out[r["ref"]] = (Attention(), fields)
            elif state is None:
                out[r["ref"]] = None
            else:
                n = state.get(r["notice_id"])
                # a notice the store no longer holds was pruned after it was read: never nag about it
                out[r["ref"]] = ((Attention(seen=True), fields) if not isinstance(n, dict) else
                                 (Attention(seen=bool(n.get("read")), acted=bool(n.get("acted"))), fields))
        return out

    def in_app(self, uid: str, row: dict, fields: dict) -> Optional[str]:
        return self._notice(uid, "card_slipped" if row.get("kind") == "slip" else "card_waiting",
                            row, fields)

    def _notice(self, to: str, kind: str, row: dict, fields: dict) -> Optional[str]:
        from flows_steps.common import door_headers, http
        base = self._agent()
        if not base:
            return None
        cid = card_id(row["ref"]) or row["ref"]
        body = {"kind": kind, "to": str(to), "object_id": str(cid)[:200], "object_kind": "card",
                "title": str(fields.get("title") or "")[:1000],
                "actor_name": str(fields.get("who") or "")[:254] or None,
                "url": row.get("link") or None}
        body = {k: v for k, v in body.items() if v}
        try:
            code, ans = http("POST", f"{base}/internal/notifications",
                             door_headers(), body, timeout=5)
        except Exception:  # noqa: BLE001 — the bell is a publish, never a dependency
            return None
        if 200 <= int(code or 0) < 300 and isinstance(ans, dict):
            ids = ans.get("ids") or []
            return str(ids[0]) if ids else ""
        return None

    def send(self, to: str, subject: str, body: str, link: str) -> str:
        from flows_steps import notify
        return notify.notify(to, subject, body, link=link)

    def failed(self, owner: str, row: dict, fields: dict) -> None:
        if owner:
            self._notice(owner, "delivery_failed", row, fields)
