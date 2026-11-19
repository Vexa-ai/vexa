"""WHERE A PERSON'S OWN WORKSPACE LIVES — `cloud` (server-side, the default) or `local` (a directory
on their own machine that their agent manages with its native file tools). ADR-0036 decision 5.

A fact about the PERSON, so identity keeps it beside their settings (`person_settings.py`). The
dogfood control rig kept it in a rig-local store, which meant no other surface could read it and it
was lost with the rig (Vexa-ai/vexa#1783).

Group workspaces are unaffected in either mode: they are shared and the server writes into them.
"""
from __future__ import annotations

import time
from typing import Optional

DATA_KEY = "workspace_regime"
MODES = ("cloud", "local")
DEFAULT = {"mode": "cloud"}


class Refused(ValueError):
    """A mode or path this vocabulary does not accept. ``detail`` is safe to return."""

    def __init__(self, detail: dict) -> None:
        super().__init__(str(detail))
        self.detail = detail


def read(data: Optional[dict]) -> dict:
    """The person's regime; `cloud` when none was ever set. Never raises."""
    raw = (data or {}).get(DATA_KEY)
    if isinstance(raw, dict) and raw.get("mode") in MODES:
        return dict(raw)
    return dict(DEFAULT)


def apply(data: Optional[dict], mode: str, local_path: str = "", now: Optional[float] = None) -> dict:
    """A copy of ``data`` with the regime set. Validated before anything is written."""
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        raise Refused({"refused": f"there is no workspace regime called {mode!r}",
                       "the_modes_that_exist": list(MODES)})
    rec: dict = {"mode": mode, "set_at": now if now is not None else time.time()}
    if mode == "local":
        path = (local_path or "").strip()
        if not path.startswith("/"):
            raise Refused({"refused": "local mode needs an ABSOLUTE local_path on the person's machine",
                           "give_me": "the directory their agent keeps personal knowledge in"})
        rec["local_path"] = path
    out = dict(data or {})
    out[DATA_KEY] = rec
    return out


def guidance(rec: dict) -> dict:
    """What the agent does next, in the regime it just set."""
    if rec.get("mode") != "local":
        return {"carry_on": "personal knowledge is server-side — use workspace_read/write as before"}
    return {"for_you_the_agent": [
        f"Create {rec.get('local_path')} if needed and manage personal knowledge there with your "
        "native file tools — no workspace_* calls for personal files from now on.",
        "Group workspaces (slug=...) STAY on workspace_* — they are shared and the server writes "
        "into them.",
        "Company claims still go through propose()/validate() — flows read them at processing "
        "time, so they cannot live only on a laptop.",
        "At the start of a session call workspace_status() — it fetches without changing anything — "
        "and, when it reports the workspace behind, workspace_pull() to mirror new flow outputs "
        "(meeting docs) down into the local directory. Never run git yourself.",
    ]}
