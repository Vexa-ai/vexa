"""The mailbox's own health — what the poll loop last saw, and the probe that reads it.

`flows-mailbox` is a `while True: poll(); sleep()` loop with no port, so there was nothing for an
orchestrator to ask, and a wrong password or an untrusted certificate showed only as a line in a
log nobody was tailing. This module gives the loop one place to say how the last poll went and
gives the chart an exec probe that reads it:

    the loop     `Reporter.ok(inbox)` after a clean poll, `Reporter.fault(err)` with the typed
                 `MailTransportError`; a fault is logged ONCE when it starts (and again when its
                 kind or detail changes) and once when it clears — never once per poll.
    the probe    `python -m flows_integrations.mailbox_status` exits 0 when the last poll was clean
                 and recent, 1 otherwise, printing the fault — so `kubectl describe pod` shows
                 `mail:auth imap mail.example.org:993 — …` under the readiness failure.

The file is the PROCESS'S OWN, in its temp directory (the chart mounts an emptyDir there), written
by one writer and read by the probe in the same container. It is not shared state and holds no
credential: `MailTransportError.as_status` is built from the kind, the wire, the host, the port and
the server's own words, none of which is ever handed a password.

WHY READINESS AND NOT LIVENESS: a wrong password is not cured by a restart, and a restart loop is
exactly the "broken deployment" picture the operator should not see for what is a configuration
answer the Exchange admins owe. NotReady, with the reason, and the loop keeps retrying — it
recovers by itself the poll after the mailbox is fixed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

#: How old the last report may be before the probe calls the loop stuck. A poll writes every
#: POLL_SECONDS (12s); a long first fetch of a busy mailbox can take minutes, so this is generous.
STALE_AFTER_S = 600


def status_path() -> str:
    return os.path.join(tempfile.gettempdir(), "vexa-mailbox-status.json")


class Reporter:
    """The loop's half. One instance per process; it remembers the last state so it logs changes."""

    def __init__(self, path: str | None = None, clock=time.time, out=print) -> None:
        self.path = path or status_path()
        self.clock = clock
        self.out = out
        self._last: tuple | None = None

    def _write(self, doc: dict) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, self.path)
        except OSError as e:
            # The probe will report the loop stale, which is true enough; the log says why.
            self.out(f"mailbox status not writable at {self.path}: {e}", flush=True)

    def ok(self, inbox: str) -> None:
        if self._last is not None and self._last[0] == "fault":
            self.out(f"mailbox RECOVERED · inbox {inbox} reachable again", flush=True)
        self._last = ("ok",)
        self._write({"state": "ok", "inbox": inbox, "at": self.clock()})

    def fault(self, err) -> None:
        st = err.as_status()
        key = ("fault", st["kind"], st["detail"])
        if key != self._last:
            self.out(f"mailbox FAULT {err}", flush=True)
        self._last = key
        self._write({"state": "fault", **st, "at": self.clock()})


def check(path: str | None = None, now: float | None = None) -> tuple[bool, str]:
    """The probe's half: (ready, one line saying why)."""
    p = path or status_path()
    try:
        with open(p) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return False, "mailbox has not completed a first poll yet"
    except (OSError, ValueError) as e:
        return False, f"mailbox status unreadable: {e}"
    age = (now if now is not None else time.time()) - float(doc.get("at") or 0)
    if age > STALE_AFTER_S:
        return False, f"mailbox loop silent for {int(age)}s (last state {doc.get('state')!r})"
    if doc.get("state") == "ok":
        return True, f"mailbox ok · inbox {doc.get('inbox')}"
    return False, (f"mail:{doc.get('kind')} {doc.get('wire')} {doc.get('host')}:{doc.get('port')}"
                   f" — {doc.get('detail')}")


def main() -> int:
    ready, why = check()
    print(why, flush=True)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
