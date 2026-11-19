"""A rig for the delivery gate: the sqlite double, a fake clock and fake doors (PRD 0.13.4 S6).

`FakePorts` stands in for crm (seen / acted / the template fields), agent-api (the in-app notice and
its read state), identity (policy, timezone, address) and the mail channel. The mail channel is a
SINK — a list — so no test can reach a mailbox.
"""
from __future__ import annotations

from flows import FakeClock
from flows_delivery import policy as P
from flows_delivery.gate import Attention
from sqlite_double import SqliteDB

# Friday 2026-10-16 13:00 UTC = 14:00 in Lisbon (WEST, UTC+1)
T0 = P.utc(2026, 10, 16, 13, 0)


class FakePorts:
    ui = "https://app.vexa.test"

    def __init__(self) -> None:
        self.policy: dict = {}
        self.tz = "Europe/Lisbon"
        self.address = "valeriia@example.test"
        self.cards: dict = {}            # card id -> dict(open, seen, acted, next_actor, title, from_name, ...)
        self.notices: dict = {}          # notice id -> dict(read, acted)
        self.sent: list = []             # (to, subject, body, link)
        self.in_apps: list = []          # (uid, ref, fields)
        self.failures: list = []
        self.attention_down = False
        self.person_down = False
        self.mail_error: Exception | None = None
        self._n = 0

    # identity
    def person(self, uid):
        if self.person_down:
            raise RuntimeError("identity down")
        return self.policy, self.tz, self.address

    # crm + agent-api read state
    def attention(self, uid, rows):
        if self.attention_down:
            raise RuntimeError("crm down")
        out = {}
        for r in rows:
            ref = r["ref"]
            if ref.startswith("card:"):
                c = self.cards.get(ref[5:])
                if c is None:
                    out[ref] = (Attention(open=False), {})
                    continue
                mine = not c.get("next_actor") or c["next_actor"] == uid
                out[ref] = (Attention(open=c.get("open", True) and mine, seen=c.get("seen", False),
                                      acted=c.get("acted", False)),
                            {"title": c.get("title", ""), "who": c.get("from_name", ""),
                             "workspace": c.get("workspace_name", "")})
            else:
                names = r.get("names") or {}
                n = self.notices.get(r.get("notice_id") or "", {})
                out[ref] = (Attention(seen=bool(n.get("read")), acted=bool(n.get("acted"))),
                            {"who": names.get("who", ""), "room": names.get("room", ""),
                             "workspace": names.get("workspace", ""), "title": names.get("title", "")})
        return out

    # agent-api
    def in_app(self, uid, row, fields):
        self._n += 1
        self.in_apps.append((uid, row["ref"], dict(fields)))
        nid = f"n{self._n}"
        self.notices[nid] = {"read": False}
        return nid

    # mail: a sink
    def send(self, to, subject, body, link):
        if self.mail_error is not None:
            raise self.mail_error
        self.sent.append((to, subject, body, link))
        return f"<m{len(self.sent)}@vexa.test>"

    def failed(self, owner, row, fields):
        self.failures.append((owner, row["ref"]))


def rig():
    return SqliteDB(), FakeClock(T0), FakePorts()
